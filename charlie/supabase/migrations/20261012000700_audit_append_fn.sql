-- Writers only have INSERT on audit.log (no SELECT), so INSERT ... RETURNING is not allowed.
-- audit.append() inserts one row and returns only that row's chain fields. Writers can never
-- read other entries.
create or replace function audit.append(p_actor text, p_kind text, p_data jsonb)
returns table(seq bigint, ts timestamptz, actor text, kind text, data jsonb, prev_hash text, hash text)
language plpgsql security definer set search_path = audit, pg_temp as $$
begin
  return query
  insert into audit.log as l (actor, kind, data) values (p_actor, p_kind, p_data)
  returning l.seq, l.ts, l.actor, l.kind, l.data, l.prev_hash, l.hash;
end $$;

revoke all on function audit.append(text, text, jsonb) from public;
grant execute on function audit.append(text, text, jsonb)
  to executor_svc, gateway_svc, worker_svc, charlie_agent;
