-- Immutable, hash-chained audit log.
-- * Each row stores prev_hash and hash = sha256(prev_hash || '|' || seq || '|' || ts || '|' || actor || '|' || kind || '|' || data).
-- * The hash is computed by a BEFORE INSERT trigger under an advisory lock, so callers can
--   neither choose nor forge it.
-- * UPDATE, DELETE and TRUNCATE are blocked by triggers (which also fire for the owner).
-- * audit.verify_chain() recomputes the whole chain.
-- * The worker exports signed batches to object storage with object lock (WORM).

create table if not exists audit.log (
  seq        bigint primary key,
  ts         timestamptz not null default now(),
  actor      text not null check (length(actor) between 1 and 64),
  kind       text not null check (length(kind) between 1 and 64),
  data       jsonb not null default '{}'::jsonb,
  prev_hash  text not null,
  hash       text not null unique
);

create sequence if not exists audit.log_seq;

create or replace function audit._row_hash(p_prev text, p_seq bigint, p_ts timestamptz, p_actor text, p_kind text, p_data jsonb)
returns text language sql immutable as $$
  select encode(sha256(convert_to(
    p_prev || '|' || p_seq::text || '|' || to_char(p_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
    p_actor || '|' || p_kind || '|' || p_data::text, 'UTF8')), 'hex')
$$;

create or replace function audit._before_insert() returns trigger
language plpgsql security definer set search_path = audit, pg_temp as $$
declare v_prev text;
begin
  perform pg_advisory_xact_lock(7243001);
  -- seq is assigned only after taking the lock, so seq order == chain order.
  new.seq := nextval('audit.log_seq');
  select hash into v_prev from audit.log order by seq desc limit 1;
  new.prev_hash := coalesce(v_prev, repeat('0', 64));
  new.ts := clock_timestamp();
  new.hash := audit._row_hash(new.prev_hash, new.seq, new.ts, new.actor, new.kind, new.data);
  return new;
end $$;

create or replace function audit._block_change() returns trigger
language plpgsql as $$
begin
  raise exception 'audit.log is append-only (% blocked)', tg_op using errcode = 'insufficient_privilege';
end $$;

drop trigger if exists audit_log_hash on audit.log;
create trigger audit_log_hash before insert on audit.log
  for each row execute function audit._before_insert();

drop trigger if exists audit_log_no_update on audit.log;
create trigger audit_log_no_update before update or delete on audit.log
  for each row execute function audit._block_change();

drop trigger if exists audit_log_no_truncate on audit.log;
create trigger audit_log_no_truncate before truncate on audit.log
  for each statement execute function audit._block_change();

create or replace function audit.verify_chain()
returns table(ok boolean, rows_checked bigint, first_bad_seq bigint)
language plpgsql stable as $$
declare r record; v_prev text := repeat('0', 64); n bigint := 0;
begin
  for r in select * from audit.log order by seq loop
    n := n + 1;
    if r.prev_hash <> v_prev or r.hash <> audit._row_hash(r.prev_hash, r.seq, r.ts, r.actor, r.kind, r.data) then
      return query select false, n, r.seq; return;
    end if;
    v_prev := r.hash;
  end loop;
  return query select true, n, null::bigint;
end $$;

revoke all on audit.log from public;
grant insert on audit.log to executor_svc, gateway_svc, worker_svc, charlie_agent;
grant select on audit.log to worker_svc, executor_svc;
grant execute on function audit.verify_chain() to worker_svc, executor_svc;
