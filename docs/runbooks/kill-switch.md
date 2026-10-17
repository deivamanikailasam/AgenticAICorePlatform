# Kill switch: stop an agent, a tool or a tenant

**Use when:** an agent, a tool or a tenant is doing harm, or might, and it must stop now: a
prompt injection that got through, a tool calling the wrong system, runaway cost, a compromised
tenant account, or an order from security or legal. No alert pages for this on its own; it is
the action other runbooks escalate to.

## What each lever stops

Pull the narrowest lever that stops the harm. All of them are audited, and none needs a new
image.

| Lever | Where | Stops | Takes effect |
|---|---|---|---|
| Policy kill switch, by agent | `kill_switch.agents.<agent>: true` in `policies/agent/config/data.json` | Every tool call the agent makes; delegations to it; A2A calls to it. An outside MCP client is an agent here too, `mcp-client:<its Keycloak client ID>` | At the next policy decision, once OPA has the new data. Runs already in flight are stopped at their next tool call |
| Policy kill switch, by tenant | `kill_switch.tenants.<tenant>: true`, same file | Every tool call for that tenant, whatever the agent, in runs and through the MCP gateway | Same |
| Feature flag, agent off | `flags: { enabled: false }` under the agent in `deploy/releases/<env>.yaml` | New runs of the agent (refused) | When Argo CD has synced and the run API's pods have restarted (the releases file is a generated ConfigMap, so a change rolls them) |
| Feature flag, tools off | `flags: { disabled_tools: [<tool>] }`, same file | Calls to the named tools by that agent (each call refused) | Same |
| Abort a candidate | `just promote <agent> --abort` | A canary or shadow version; the stable version serves | Same |
| Cancel a run | `POST /v1/runs/{id}/cancel` (owner) or `POST /v1/runs/{id}/takeover` (operator) | One run in flight | At once |

An irreversible tool waits for a person's approval in every case. Rejecting a pending approval
(`POST /v1/runs/{id}/approvals` with `{"decision": "reject"}`) stops that one action, and
nothing behind it runs.

## Do it

1. **Stop the harm with the policy kill switch.** It is the fastest lever that covers every
   path: runs, delegations, A2A and the MCP gateway.
   - In Git: open a pull request that sets the switch in `policies/agent/config/data.json`. It
     needs the policy's code owners. In staging and prod, OPA loads `policies/agent/**` as a
     signed bundle (`docs/setup/production.md`); the merged change reaches OPA when the bundle
     is next published.
   - In an emergency, when a pull request is too slow: an operator with write access to the
     bundle server publishes the same change there, and opens the pull request straight after so
     Git matches what is enforced. The bundle server's access log and OPA's decision log record
     who did it and when.
   - In dev and CI: edit the file and run `just provision-policies`.
2. **Check it holds.** Every tool call from the agent is now denied with "tool execution is
   disabled for this agent (kill switch)" (or "... for this tenant ..."). Find them in the audit
   trail: `policy.decision` events with that reason, and no `tool.call` that ran.
3. **Stop new runs too** if the agent shouldn't start at all: set `enabled: false` in the
   environment's releases file by pull request (`deploy/releases/prod.yaml` is a governance path
   and needs its named approver).
4. **Deal with what is in flight.** `GET /v1/approvals` shows actions waiting for a person:
   reject the ones from the stopped agent. Cancel or take over runs that are still running.
5. **Keep the evidence.** Do not delete runs or memories during an incident. The audit trail is
   hash-chained (`just audit-verify`), and a run can be rebuilt from it and its checkpoints
   (Step 31).

## Turn it back on

- Only after the cause is fixed, and as a **new version** if the agent itself was at fault. A
  version that was rolled back never serves again.
- Remove the switch by a pull request reviewed by the same owners. Check that a test run
  succeeds in staging first.

## Tested

- The red-team suite's ASI10 attacks set the agent and the tenant kill switches and check that
  every tool call is denied (`evals/redteam/attacks/asi10-rogue-agents.yaml`). They run in CI on
  every push against the live OPA.
- The feature flags are tested in `services/agent-api/tests/test_releases.py`
  (`test_feature_flags_switch_an_agent_or_a_tool_off`); aborting a canary in
  `libs/agent-governance/tests/test_promotion.py`.

## Escalate

To the agent's owner (`owner` in its spec) and the platform's security owner (the threat model's
owner for the component). If personal data may have left the platform, to the privacy officer.
