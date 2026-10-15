-- Step 18: the run API's runs and their AG-UI event logs survive a restart. Run details and
-- events are encrypted by the application; only what is needed to find a run is in clear.
create table if not exists agent_runtime.runs (
    run_id          text        primary key,
    tenant_id       text        not null,
    user_id         text        not null,
    thread_id       text        not null,
    agent           text        not null,
    status          text        not null,
    idempotency_key text,
    body_hash       text,
    doc             text        not null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists runs_owner on agent_runtime.runs (tenant_id, user_id, created_at);
create unique index if not exists runs_idempotency on agent_runtime.runs (tenant_id, user_id, idempotency_key)
    where idempotency_key is not null;

create table if not exists agent_runtime.run_events (
    run_id text    not null references agent_runtime.runs (run_id) on delete cascade,
    idx    integer not null,
    event  text    not null,
    primary key (run_id, idx)
);
