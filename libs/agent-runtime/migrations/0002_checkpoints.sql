-- Step 18: a snapshot after every node, keyed by tenant, thread and run. Sensitive state fields
-- are encrypted by the application (AES-256-GCM) before they reach this table.
create table if not exists agent_runtime.checkpoints (
    seq           bigserial   primary key,
    tenant_id     text        not null,
    thread_id     text        not null,
    checkpoint_id text        not null unique,
    parent_id     text,
    run_id        text,
    step          integer     not null default 0,
    node_id       text,
    state         jsonb       not null,
    created_at    timestamptz not null default now()
);
create index if not exists checkpoints_thread on agent_runtime.checkpoints (tenant_id, thread_id, seq);
create index if not exists checkpoints_run on agent_runtime.checkpoints (tenant_id, run_id, seq);
create index if not exists checkpoints_age on agent_runtime.checkpoints (created_at);
