-- Step 38: release state shared by every run API replica. A rolled-back candidate version never
-- serves again; outcomes are the online evaluation's scored runs (no content: version, success,
-- cost, duration).
create table if not exists agent_runtime.release_state (
    agent   text        not null,
    version text        not null,
    state   text        not null check (state in ('rolled_back')),
    reason  text        not null,
    at      timestamptz not null default now(),
    primary key (agent, version)
);

create table if not exists agent_runtime.release_outcomes (
    agent    text             not null,
    version  text             not null,
    run_id   text             not null,
    mode     text             not null check (mode in ('stable', 'canary', 'shadow')),
    ok       boolean          not null,
    cost_aud double precision not null,
    seconds  double precision not null,
    at       timestamptz      not null default now(),
    primary key (run_id, mode)
);
create index if not exists release_outcomes_version on agent_runtime.release_outcomes (agent, version, at);
