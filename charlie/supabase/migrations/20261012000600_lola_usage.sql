-- Lola's own usage ledger (LLM cost + tool calls), in the guest schema so Lola's role can
-- read/write it without any access to core. Feeds Lola's own hard caps.

create table if not exists guest.lola_usage (
  id         bigserial primary key,
  ts         timestamptz not null default now(),
  kind       text not null check (kind in ('llm','tool')),
  tool       text,
  channel    text not null,
  model      text,
  input_tokens  int not null default 0,
  output_tokens int not null default 0,
  cost_nok   numeric(12,4) not null default 0,
  day        date not null,
  month      text not null
);
create index if not exists lola_usage_day on guest.lola_usage (day);

grant select, insert on guest.lola_usage to lola_agent;
grant select on guest.lola_usage to worker_svc;
grant usage on sequence guest.lola_usage_id_seq to lola_agent;
alter table guest.lola_usage enable row level security;
alter table guest.lola_usage force row level security;
drop policy if exists lola_rw on guest.lola_usage;
create policy lola_rw on guest.lola_usage for all to lola_agent using (true) with check (true);
drop policy if exists worker_read on guest.lola_usage;
create policy worker_read on guest.lola_usage for select to worker_svc using (true);

-- Heartbeats from Lola go here too (she cannot write core.heartbeats).
create table if not exists guest.lola_heartbeat (
  id        int primary key default 1 check (id = 1),
  last_seen timestamptz not null
);
grant select, insert, update on guest.lola_heartbeat to lola_agent;
grant select on guest.lola_heartbeat to worker_svc;
alter table guest.lola_heartbeat enable row level security;
alter table guest.lola_heartbeat force row level security;
drop policy if exists lola_rw on guest.lola_heartbeat;
create policy lola_rw on guest.lola_heartbeat for all to lola_agent using (true) with check (true);
drop policy if exists worker_read on guest.lola_heartbeat;
create policy worker_read on guest.lola_heartbeat for select to worker_svc using (true);
