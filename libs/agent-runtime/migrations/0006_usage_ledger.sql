-- Step 26: every model call's tokens and cost, the platform's side of cost accounting.
-- cost_usd is exactly what LiteLLM reported; call_id is the response id (LiteLLM's request_id);
-- key_name is the role whose virtual key paid (a role's own key or a fallback).
create table if not exists agent_runtime.usage_ledger (
    id            bigserial   primary key,
    at            timestamptz not null default now(),
    run_id        text        not null,
    role          text        not null,
    alias         text        not null,
    input_tokens  integer     not null,
    output_tokens integer     not null,
    cost_aud      numeric     not null,
    cost_usd      numeric     not null,
    call_id       text,
    key_name      text        not null  -- whose gateway key paid: reconciles per key
);
create index if not exists usage_ledger_by_time on agent_runtime.usage_ledger (at, key_name);
create index if not exists usage_ledger_by_run on agent_runtime.usage_ledger (run_id);
