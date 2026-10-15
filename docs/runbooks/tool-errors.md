# AgentToolErrorRateHigh

**Severity:** ticket · **Fires when:** more than 20% of calls to one tool fail for 10 minutes.

## What it means
A tool keeps failing: timeouts, errors, refusals by policy (`denied`), missing approvals, rate
limits or an open circuit (`unavailable`). The `error_type` label on
`gen_ai_execute_tool_duration_seconds_count` says which.

## Check
1. Grafana → *Tool error rate*; Prometheus: `sum by (error_type) (rate(gen_ai_execute_tool_duration_seconds_count{gen_ai_tool_name="<tool>"}[5m]))`.
2. `denied` / `approval_required`: a policy change (OPA bundle, `policies/agent/config/data.json`).
3. `timeout` / `failed` / `unavailable`: the tool's service; MCP spans (`tools/call <tool>`) show
   the server and error.
4. Credentialed tools (`credentials:` in the spec): OpenBao reachable and the agent's OpenBao
   role present (`just provision-openbao`).

## Fix
Restore the service, roll back the policy change, or re-provision OpenBao. The circuit breaker
closes again by itself after its cooldown.

## Escalate
The tool's owner (tool card `owner`), or Project 0 for its services.
