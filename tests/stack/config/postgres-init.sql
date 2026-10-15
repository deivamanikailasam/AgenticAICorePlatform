-- Stand-in only: roles and databases the Project 0 services would already have.
CREATE DATABASE litellm;
CREATE ROLE agent_health LOGIN PASSWORD 'standin-only';  -- health probe, no privileges
GRANT CONNECT ON DATABASE postgres TO agent_health;
CREATE DATABASE langfuse;  -- Langfuse (Step 30)
