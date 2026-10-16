-- Step 38: with several run API replicas, each unfinished run is driven by the replica holding
-- its lease. A lapsed lease (the replica stopped renewing it) lets another replica take over.
alter table agent_runtime.runs add column if not exists lease_owner text;
alter table agent_runtime.runs add column if not exists lease_until timestamptz;
