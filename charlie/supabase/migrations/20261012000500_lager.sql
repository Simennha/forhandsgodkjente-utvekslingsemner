-- Inventory ("lager"): nine tables. Norway and Switzerland are independent:
-- each location orders on its own and there is NO transfer between them.
-- A move between shelves is two `bevegelse` rows with arsak 'flytt' sharing a flytt_id,
-- inside one location. beholdning is maintained only by triggers.

create table if not exists lager.lokasjon (
  id      text primary key check (id in ('NO','CH')),
  navn    text not null,
  land    text not null,
  valuta  text not null
);

create table if not exists lager.lagerplass (
  id           serial primary key,
  lokasjon_id  text not null references lager.lokasjon(id),
  kode         text not null,                -- e.g. R3-H2, ESD-1
  reol         text not null,                -- R1..R11 or ESD
  hylle        int not null check (hylle between 1 and 10),
  esd          boolean not null default false,
  unique (lokasjon_id, kode)
);

create table if not exists lager.leverandor (
  id                  text primary key,
  navn                text not null,
  land                text not null,
  epost               text,
  nettside            text,
  leveringstid_dager  int not null check (leveringstid_dager > 0),
  valuta              text not null
);

create table if not exists lager.artikkel (
  id               serial primary key,
  sku              text not null unique,
  navn             text not null,
  kategori         text not null,
  enhet            text not null default 'stk',
  esd_sensitiv     boolean not null default false,
  pris_nok         numeric(12,2) not null check (pris_nok >= 0),
  leverandor_id    text references lager.leverandor(id),
  leverandor_sku   text
);

create table if not exists lager.lagerregel (
  artikkel_id  int not null references lager.artikkel(id),
  lokasjon_id  text not null references lager.lokasjon(id),
  min_antall   int not null check (min_antall >= 0),
  maks_antall  int not null,
  primary key (artikkel_id, lokasjon_id),
  check (maks_antall >= min_antall and maks_antall > 0)
);

create table if not exists lager.beholdning (
  artikkel_id    int not null references lager.artikkel(id),
  lagerplass_id  int not null references lager.lagerplass(id),
  antall         int not null check (antall >= 0),
  primary key (artikkel_id, lagerplass_id)
);

create table if not exists lager.bevegelse (
  id             bigserial primary key,
  ts             timestamptz not null default now(),
  artikkel_id    int not null references lager.artikkel(id),
  lagerplass_id  int not null references lager.lagerplass(id),
  endring        int not null check (endring <> 0),
  arsak          text not null check (arsak in ('mottak','uttak','flytt','telling','svinn')),
  flytt_id       uuid,
  utfort_av      text not null,
  kommentar      text,
  check ((arsak = 'flytt') = (flytt_id is not null))
);
create index if not exists bevegelse_flytt on lager.bevegelse (flytt_id) where flytt_id is not null;

create table if not exists lager.batteri (
  id              serial primary key,
  serienr         text not null unique,
  artikkel_id     int references lager.artikkel(id),
  lagerplass_id   int not null references lager.lagerplass(id),
  kjemi           text not null check (kjemi in ('LiPo','Li-ion','LiFePO4','NiMH','Pb')),
  celler          int not null check (celler > 0),
  kapasitet_mah   int not null check (kapasitet_mah > 0),
  sykluser        int not null default 0 check (sykluser >= 0),
  sist_ladet      date,
  lagringsspenning boolean not null default true,
  status          text not null default 'ok' check (status in ('ok','lagring','defekt','resirkuler'))
);

create table if not exists lager.innkjopsforslag (
  id                 bigserial primary key,
  generert           timestamptz not null default now(),
  lokasjon_id        text not null references lager.lokasjon(id),
  artikkel_id        int not null references lager.artikkel(id),
  leverandor_id      text references lager.leverandor(id),
  beholdning_naa     int not null,
  min_antall         int not null,
  maks_antall        int not null,
  foreslatt_antall   int not null check (foreslatt_antall > 0),
  estimert_pris_nok  numeric(12,2) not null,
  status             text not null default 'ny' check (status in ('ny','bestilt','avvist'))
);

-- Keep beholdning in sync with bevegelse; refuse negative stock.
create or replace function lager._apply_bevegelse() returns trigger
language plpgsql security definer set search_path = lager, pg_temp as $$
begin
  update lager.beholdning set antall = antall + new.endring
   where artikkel_id = new.artikkel_id and lagerplass_id = new.lagerplass_id;
  if not found then
    if new.endring < 0 then
      raise exception 'negativ beholdning for artikkel % på plass %', new.artikkel_id, new.lagerplass_id;
    end if;
    insert into lager.beholdning (artikkel_id, lagerplass_id, antall)
    values (new.artikkel_id, new.lagerplass_id, new.endring);
  end if;
  return new;
end $$;

drop trigger if exists bevegelse_apply on lager.bevegelse;
create trigger bevegelse_apply after insert on lager.bevegelse
  for each row execute function lager._apply_bevegelse();

-- ESD-sensitive articles may only be stored in ESD-protected places.
create or replace function lager._check_esd() returns trigger
language plpgsql as $$
begin
  if new.antall > 0
     and (select esd_sensitiv from lager.artikkel where id = new.artikkel_id)
     and not (select esd from lager.lagerplass where id = new.lagerplass_id) then
    raise exception 'ESD-sensitiv artikkel % kan ikke lagres utenfor ESD-sone', new.artikkel_id;
  end if;
  return new;
end $$;

drop trigger if exists beholdning_esd on lager.beholdning;
create trigger beholdning_esd before insert or update on lager.beholdning
  for each row execute function lager._check_esd();

-- A move (flytt) must be exactly two rows: same article, same location, different places,
-- equal and opposite quantities. Checked at commit (deferred constraint trigger).
create or replace function lager._check_flytt() returns trigger
language plpgsql as $$
declare n int; s int; arts int; locs int; places int;
begin
  if new.flytt_id is null then return null; end if;
  select count(*), sum(b.endring), count(distinct b.artikkel_id),
         count(distinct p.lokasjon_id), count(distinct b.lagerplass_id)
    into n, s, arts, locs, places
    from lager.bevegelse b join lager.lagerplass p on p.id = b.lagerplass_id
   where b.flytt_id = new.flytt_id;
  if n <> 2 or s <> 0 or arts <> 1 or places <> 2 then
    raise exception 'ugyldig flytt %: må være to motsatte bevegelser mellom to plasser', new.flytt_id;
  end if;
  if locs <> 1 then
    raise exception 'flytt mellom Norge og Sveits er ikke tillatt';
  end if;
  return null;
end $$;

drop trigger if exists bevegelse_flytt_check on lager.bevegelse;
create constraint trigger bevegelse_flytt_check after insert on lager.bevegelse
  deferrable initially deferred
  for each row execute function lager._check_flytt();

-- Movements are history: never updated or deleted.
create or replace function lager._block_change() returns trigger language plpgsql as $$
begin raise exception 'bevegelse er historikk og kan ikke endres'; end $$;
drop trigger if exists bevegelse_immutable on lager.bevegelse;
create trigger bevegelse_immutable before update or delete on lager.bevegelse
  for each row execute function lager._block_change();

create or replace view lager.beholdning_per_lokasjon as
  select a.id as artikkel_id, a.sku, a.navn, p.lokasjon_id, coalesce(sum(b.antall), 0)::int as antall
    from lager.artikkel a
    cross join lager.lokasjon l
    left join lager.lagerplass p on p.lokasjon_id = l.id
    left join lager.beholdning b on b.lagerplass_id = p.id and b.artikkel_id = a.id
   where p.lokasjon_id is not null
   group by a.id, a.sku, a.navn, p.lokasjon_id;

-- Generate purchase suggestions for ONE location from lagerregel + beholdning.
-- Replaces earlier 'ny' suggestions for that location.
create or replace function lager.generer_innkjopsforslag(p_lokasjon text)
returns setof lager.innkjopsforslag
language plpgsql security definer set search_path = lager, pg_temp as $$
begin
  if p_lokasjon not in ('NO','CH') then
    raise exception 'ukjent lokasjon %', p_lokasjon;
  end if;
  delete from lager.innkjopsforslag where lokasjon_id = p_lokasjon and status = 'ny';
  return query
  insert into lager.innkjopsforslag
    (lokasjon_id, artikkel_id, leverandor_id, beholdning_naa, min_antall, maks_antall,
     foreslatt_antall, estimert_pris_nok)
  select r.lokasjon_id, r.artikkel_id, a.leverandor_id, coalesce(h.antall, 0),
         r.min_antall, r.maks_antall,
         r.maks_antall - coalesce(h.antall, 0),
         (r.maks_antall - coalesce(h.antall, 0)) * a.pris_nok
    from lager.lagerregel r
    join lager.artikkel a on a.id = r.artikkel_id
    left join (
      select b.artikkel_id, sum(b.antall)::int as antall
        from lager.beholdning b join lager.lagerplass p on p.id = b.lagerplass_id
       where p.lokasjon_id = p_lokasjon
       group by b.artikkel_id
    ) h on h.artikkel_id = r.artikkel_id
   where r.lokasjon_id = p_lokasjon
     and coalesce(h.antall, 0) < r.min_antall
  returning *;
end $$;

-- Grants
grant select on all tables in schema lager to charlie_agent, executor_svc, worker_svc;
grant insert on lager.bevegelse to executor_svc;
grant update (status) on lager.innkjopsforslag to executor_svc;
grant usage on all sequences in schema lager to executor_svc;
grant execute on function lager.generer_innkjopsforslag(text) to executor_svc, worker_svc;
revoke execute on function lager.generer_innkjopsforslag(text) from public;
