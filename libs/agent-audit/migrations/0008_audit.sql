-- Step 31: the agent audit trail, one hash chain for every writer (agent-api, agent-worker,
-- mcp-gateway, a2a-gateway). Each row's hash covers the previous row's hash, its own sequence
-- number and the event, so changing, removing or reordering any row breaks the chain from there.
create schema if not exists agent_audit;

create table if not exists agent_audit.events (
    seq        bigint primary key check (seq >= 0),
    prev_hash  text not null,
    hash       text not null unique,
    type       text not null,
    tenant_id  text not null,
    run_id     text not null,
    agent_id   text not null,
    trace_id   text,
    at         timestamptz not null,
    event      jsonb not null           -- exactly what was hashed (AuditEvent.chained())
);

create index if not exists events_run on agent_audit.events (run_id, seq);
create index if not exists events_tenant_at on agent_audit.events (tenant_id, at);
create index if not exists events_trace on agent_audit.events (trace_id);

-- append-only: updates, deletes and truncation are refused, even for the owning role. Someone
-- with database-admin rights could still bypass this; the hash chain is what detects that.
create or replace function agent_audit.refuse_change() returns trigger
language plpgsql as $$
begin
    raise exception 'agent_audit.events is append-only (% refused)', tg_op;
end;
$$;

drop trigger if exists events_append_only on agent_audit.events;
create trigger events_append_only
    before update or delete on agent_audit.events
    for each row execute function agent_audit.refuse_change();

drop trigger if exists events_no_truncate on agent_audit.events;
create trigger events_no_truncate
    before truncate on agent_audit.events
    for each statement execute function agent_audit.refuse_change();
