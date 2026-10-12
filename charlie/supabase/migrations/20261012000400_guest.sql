-- Guest area: the ONLY data Lola can see, and Lola's one-way request queue to Charlie.
--
-- lola_agent: SELECT guest.info; INSERT guest.lola_requests and guest.messages.
-- Lola cannot read back the queue, cannot read anything in core/lager/audit, and has no
-- function she can call that touches them.
-- Charlie (agent) may read the queue; only the worker marks rows processed.

create table if not exists guest.info (
  key         text primary key,
  title       text not null,
  body        text not null,
  updated_at  timestamptz not null default now()
);

create table if not exists guest.lola_requests (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  channel       text not null check (channel in ('phone_lola','door','chat_guest','sms_lola')),
  from_number   text,
  kind          text not null check (kind in ('message','request')),
  -- Untrusted guest text. Treated as DATA: it is only ever passed to the isolated
  -- extractor, never to a model that can act.
  untrusted_text text not null check (length(untrusted_text) <= 2000),
  trust         text not null default 'untrusted' check (trust = 'untrusted'),
  processed_at  timestamptz
);

create table if not exists guest.messages (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  for_whom      text not null check (length(for_whom) <= 80),
  from_name     text check (length(from_name) <= 80),
  untrusted_text text not null check (length(untrusted_text) <= 2000)
);

revoke all on guest.info, guest.lola_requests, guest.messages from public;
grant select on guest.info to lola_agent, charlie_agent, executor_svc, worker_svc;
grant insert on guest.lola_requests, guest.messages to lola_agent;
grant select on guest.lola_requests, guest.messages to charlie_agent, worker_svc;
grant update (processed_at) on guest.lola_requests to worker_svc;
grant insert, update, delete on guest.info to executor_svc;

alter table guest.info enable row level security;
alter table guest.info force row level security;
alter table guest.lola_requests enable row level security;
alter table guest.lola_requests force row level security;
alter table guest.messages enable row level security;
alter table guest.messages force row level security;

drop policy if exists read_all on guest.info;
create policy read_all on guest.info for select using (true);
drop policy if exists exec_write on guest.info;
create policy exec_write on guest.info for all to executor_svc using (true) with check (true);

drop policy if exists lola_insert on guest.lola_requests;
create policy lola_insert on guest.lola_requests for insert to lola_agent
  with check (trust = 'untrusted' and processed_at is null);
drop policy if exists charlie_read on guest.lola_requests;
create policy charlie_read on guest.lola_requests for select to charlie_agent, worker_svc using (true);
drop policy if exists worker_mark on guest.lola_requests;
create policy worker_mark on guest.lola_requests for update to worker_svc using (true) with check (true);

drop policy if exists lola_insert on guest.messages;
create policy lola_insert on guest.messages for insert to lola_agent with check (true);
drop policy if exists charlie_read on guest.messages;
create policy charlie_read on guest.messages for select to charlie_agent, worker_svc using (true);
