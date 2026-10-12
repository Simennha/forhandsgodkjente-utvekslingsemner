-- Red-team fixes (docs/redteam-report.md).

-- #8: monthly order cap is counted by approval time.
alter table core.proposals add column if not exists approved_at timestamptz;

-- #9: every audit row records which database role wrote it. Set by the trigger from the
-- session's role, so a service can no longer pose as another one. Rows written before this
-- migration have writer = null and keep their original hash.
alter table audit.log add column if not exists writer text;

create or replace function audit._row_hash2(p_prev text, p_seq bigint, p_ts timestamptz, p_actor text,
                                            p_kind text, p_data jsonb, p_writer text)
returns text language sql immutable as $$
  select encode(sha256(convert_to(
    p_prev || '|' || p_seq::text || '|' || to_char(p_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
    p_actor || '|' || p_kind || '|' || p_data::text || coalesce('|' || p_writer, ''), 'UTF8')), 'hex')
$$;

create or replace function audit._before_insert() returns trigger
language plpgsql security definer set search_path = audit, pg_temp as $$
declare v_prev text;
begin
  perform pg_advisory_xact_lock(7243001);
  new.seq := nextval('audit.log_seq');
  select hash into v_prev from audit.log order by seq desc limit 1;
  new.prev_hash := coalesce(v_prev, repeat('0', 64));
  new.ts := clock_timestamp();
  new.writer := coalesce(nullif(current_setting('role', true), 'none'), session_user);
  new.hash := audit._row_hash2(new.prev_hash, new.seq, new.ts, new.actor, new.kind, new.data, new.writer);
  return new;
end $$;

create or replace function audit.verify_chain()
returns table(ok boolean, rows_checked bigint, first_bad_seq bigint)
language plpgsql stable as $$
declare r record; v_prev text := repeat('0', 64); n bigint := 0;
begin
  for r in select * from audit.log order by seq loop
    n := n + 1;
    if r.prev_hash <> v_prev
       or r.hash <> audit._row_hash2(r.prev_hash, r.seq, r.ts, r.actor, r.kind, r.data, r.writer) then
      return query select false, n, r.seq; return;
    end if;
    v_prev := r.hash;
  end loop;
  return query select true, n, null::bigint;
end $$;

-- audit.append() is SECURITY DEFINER: inside it, current_setting('role') is the caller's
-- SET ROLE (if any) and session_user the login role - both unforgeable by the caller.

-- #17: charlie_agent never logs in (agents-charlie has no database access at all).
alter role charlie_agent nologin;
revoke select on guest.lola_requests, guest.messages from charlie_agent;
revoke execute on function audit.append(text, text, jsonb) from charlie_agent;
revoke insert on audit.log from charlie_agent;
