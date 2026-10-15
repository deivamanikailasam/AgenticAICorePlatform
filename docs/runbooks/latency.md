# AgentLatencyHigh

**Severity:** ticket · **Fires when:** an agent's p95 run time stays above 120 seconds for 10
minutes.

## What it means
The slowest runs of this agent take more than two minutes. Causes: slow model responses, a slow
tool, more steps per run than before, or runs waiting on queues.

## Check
1. Grafana → *Run time p50 / p95*, *Model call time p95* and *Steps per run* for the agent.
2. A slow run's trace in Tempo: which spans take the time (`chat` vs `execute_tool` vs
   `tools/call`).
3. Model latency across agents (Project 0's gateway dashboards) to rule out the local model host.

## Fix
- Slow model: route the agent's role to a faster model alias in its spec or the platform roles.
- Slow tool: tool timeouts in its spec; check the tool's service.
- More steps: review recent spec or prompt changes; set `max_steps` or per-tool limits.

## Escalate
The agent's owner within a working day; Project 0 if the gateway or model host is slow for
everyone.
