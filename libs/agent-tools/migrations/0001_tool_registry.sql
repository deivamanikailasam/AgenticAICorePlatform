-- Step 6: the versioned tool registry, shared by every API and worker process.
-- `namespace` separates registries (an environment, a test run) inside one database.
create table if not exists agent_tools.tool_specs (
    namespace   text        not null default 'default',
    name        text        not null,
    version     text        not null,
    spec        jsonb       not null,
    deprecated  boolean     not null default false,
    owner       text        not null,
    side_effect text        not null check (side_effect in ('read', 'write', 'irreversible')),
    created_at  timestamptz not null default now(),
    primary key (namespace, name, version)
);
create index if not exists tool_specs_live on agent_tools.tool_specs (namespace, name) where not deprecated;
