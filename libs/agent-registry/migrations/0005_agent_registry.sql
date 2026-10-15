-- Step 25: the agent registry. One row per agent version; every lifecycle move in the history.
-- `namespace` separates registries (an environment, a test run) inside one database.
create table if not exists agent_registry.agents (
    namespace  text        not null default 'default',
    name       text        not null,
    version    text        not null,
    record     jsonb       not null,
    status     text        not null check (status in ('draft', 'approved', 'deprecated', 'retired')),
    owner      text        not null,
    spec_hash  text        not null,
    updated_at timestamptz not null default now(),
    primary key (namespace, name, version)
);
create index if not exists agents_by_status on agent_registry.agents (namespace, status);

create table if not exists agent_registry.status_history (
    seq         bigserial   primary key,
    namespace   text        not null default 'default',
    name        text        not null,
    version     text        not null,
    from_status text,
    to_status   text        not null,
    changed_by  text        not null,
    reason      text        not null default '',
    at          timestamptz not null default now()
);
create index if not exists status_history_by_agent on agent_registry.status_history (namespace, name, version, seq);
