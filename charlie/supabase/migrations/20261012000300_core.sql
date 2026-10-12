-- Core tables: events (queue), proposals, idempotency, cost ledger, heartbeats,
-- consent log, transcripts (raw: 7-day retention) and extracts, notes, projects.

create table if not exists core.events (
  id           uuid primary key,
  ts           timestamptz not null default now(),
  source       text not null,          -- phone_charlie, phone_lola, sms, chat, email, door, mic, killswitch, system
  trust        text not null check (trust in ('owner_pin','owner_known','duress','guest','unknown','system','untrusted')),
  agent        text not null check (agent in ('charlie','lola','system')),
  mode         text not null check (mode in ('normal','guest','open_workshop')),
  payload      jsonb not null,
  claimed_at   timestamptz,
  done_at      timestamptz
);
create index if not exists events_pending on core.events (agent, ts) where done_at is null;

create table if not exists core.proposals (
  id               uuid primary key,
  created_at       timestamptz not null default now(),
  agent            text not null check (agent = 'charlie'),
  tool             text not null,
  params           jsonb not null,
  params_hash      text not null,
  level            int not null check (level between 1 and 3),
  status           text not null check (status in ('pending','approved','executed','denied','expired','frozen','failed')),
  confirm_method   text check (confirm_method in ('passkey','approval_box')),
  challenge        text,
  amount_nok       numeric(12,2),
  expires_at       timestamptz,
  result           jsonb,
  idempotency_key  text not null unique
);
create index if not exists proposals_status on core.proposals (status);

create table if not exists core.used_tokens (
  jti        text primary key,
  used_at    timestamptz not null default now()
);

create table if not exists core.cost_ledger (
  id             bigserial primary key,
  ts             timestamptz not null default now(),
  agent          text not null,
  channel        text not null,
  model          text not null,
  input_tokens   int not null,
  output_tokens  int not null,
  cache_read     int not null default 0,
  cache_write    int not null default 0,
  cost_nok       numeric(12,4) not null,
  day            date not null,
  month          text not null
);
create index if not exists cost_ledger_agent_day on core.cost_ledger (agent, day);
create index if not exists cost_ledger_agent_month on core.cost_ledger (agent, month);

create table if not exists core.tool_calls (
  id     bigserial primary key,
  ts     timestamptz not null default now(),
  agent  text not null,
  tool   text not null
);
create index if not exists tool_calls_recent on core.tool_calls (agent, tool, ts);

create table if not exists core.heartbeats (
  service   text primary key,
  last_seen timestamptz not null
);

create table if not exists core.consent_log (
  id        bigserial primary key,
  ts        timestamptz not null default now(),
  call_id   text not null,
  agent     text not null,
  number    text not null,
  answer    text not null check (answer in ('accepted','declined','no_answer'))
);

create table if not exists core.transcripts_raw (
  id            uuid primary key,
  call_id       text not null,
  agent         text not null,
  created_at    timestamptz not null default now(),
  delete_after  timestamptz not null,
  text          text not null
);
create index if not exists transcripts_raw_expiry on core.transcripts_raw (delete_after);

create table if not exists core.transcript_extracts (
  id          uuid primary key,
  call_id     text not null,
  agent       text not null,
  created_at  timestamptz not null default now(),
  extract     jsonb not null
);

create table if not exists core.notes (
  id          uuid primary key,
  created_at  timestamptz not null default now(),
  text        text not null check (length(text) <= 2000),
  tags        text[] not null default '{}',
  project     text
);

create table if not exists core.projects (
  name        text primary key,
  status      text not null,
  updated_at  timestamptz not null default now(),
  history     jsonb not null default '[]'::jsonb
);

-- Grants. Charlie (agent) reads its own working data; writes go through the executor.
grant select on core.notes, core.projects, core.transcript_extracts to charlie_agent;
grant select, insert, update on core.events, core.proposals to executor_svc;
grant select, insert on core.used_tokens, core.cost_ledger, core.tool_calls to executor_svc;
grant insert, update on core.heartbeats to executor_svc, gateway_svc, worker_svc;
grant select on core.heartbeats to worker_svc;
grant select, insert, update on core.notes, core.projects to executor_svc;
grant select, insert on core.transcript_extracts to executor_svc, worker_svc;
grant insert on core.events, core.consent_log, core.transcripts_raw to gateway_svc;
grant select, update on core.events to worker_svc;
grant select, delete on core.transcripts_raw to worker_svc;
grant select on core.cost_ledger, core.proposals, core.consent_log, core.notes, core.projects to worker_svc;
grant usage on all sequences in schema core to executor_svc, gateway_svc, worker_svc;

-- RLS on everything in core (defense in depth on top of grants).
do $$
declare t text;
begin
  foreach t in array array['events','proposals','used_tokens','cost_ledger','tool_calls','heartbeats',
                           'consent_log','transcripts_raw','transcript_extracts','notes','projects']
  loop
    execute format('alter table core.%I enable row level security', t);
    execute format('alter table core.%I force row level security', t);
    execute format('drop policy if exists svc_all on core.%I', t);
    execute format($p$create policy svc_all on core.%I for all
                      to executor_svc, gateway_svc, worker_svc using (true) with check (true)$p$, t);
  end loop;
end $$;

drop policy if exists charlie_read on core.notes;
create policy charlie_read on core.notes for select to charlie_agent using (true);
drop policy if exists charlie_read on core.projects;
create policy charlie_read on core.projects for select to charlie_agent using (true);
drop policy if exists charlie_read on core.transcript_extracts;
create policy charlie_read on core.transcript_extracts for select to charlie_agent using (agent = 'charlie');
