-- Step 28: approval requests and decisions, durable across restarts.
create table if not exists agent_runtime.approvals (
    id          text        primary key,
    tenant_id   text        not null,
    run_id      text        not null,
    approvers   text        not null,
    status      text        not null check (status in ('pending', 'approved', 'rejected', 'escalated', 'expired')),
    record      jsonb       not null,
    expires_at  timestamptz,
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);
create index if not exists approvals_pending on agent_runtime.approvals (approvers, expires_at) where status = 'pending';
create index if not exists approvals_by_run on agent_runtime.approvals (run_id);
