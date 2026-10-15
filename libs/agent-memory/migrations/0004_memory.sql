-- Step 20: long-term memory. Scope and lifecycle columns stay in clear for filtering and
-- erasure; content is encrypted by the application (AES-256-GCM) when a key is configured.
-- The embedding column has no fixed dimension, so any embedding model fits; searches compare
-- only vectors of the query's dimension.
create table if not exists agent_memory.items (
    id              text        primary key,
    tenant_id       text        not null,
    user_id         text,
    agent_id        text,
    subject_user_id text,
    kind            text        not null,
    key             text,
    version         integer     not null default 1,
    content         text        not null,
    provenance      jsonb       not null,
    confidence      real        not null,
    expires_at      timestamptz,
    created_at      timestamptz not null default now(),
    embedding       vector      not null
);
create index if not exists memory_scope on agent_memory.items (tenant_id, user_id, agent_id);
create index if not exists memory_subject on agent_memory.items (tenant_id, subject_user_id);
create index if not exists memory_key on agent_memory.items (tenant_id, key, version);
create index if not exists memory_expiry on agent_memory.items (expires_at);
