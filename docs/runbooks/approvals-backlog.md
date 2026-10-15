# AgentApprovalsBacklog

**Severity:** ticket · **Fires when:** more than 20 approvals wait for one agent for 30 minutes.

## What it means
Reviewers are not keeping up. Approvals that pass their SLA escalate once and then expire,
cancelling the run, so a backlog turns into cancelled work.

## Check
1. Grafana → *Approvals pending*.
2. `GET /v1/approvals` as a member of the approver group: which groups the requests are routed to
   and their expiry.
3. Routing in `policies/agent/config/data.json` (`approvals`): is the group right and staffed?

## Fix
Add reviewers to the group in Keycloak, re-route with a reviewed policy change, or pause the
agent (deprecate the version) if it is asking for approvals it should not need.

## Escalate
The approver group's lead and the agent's owner.
