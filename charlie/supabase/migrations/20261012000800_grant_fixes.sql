-- INSERT ... ON CONFLICT DO UPDATE needs SELECT on the conflict target.
grant select on core.heartbeats to executor_svc, gateway_svc;
