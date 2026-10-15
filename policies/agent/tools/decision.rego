# Tool-call authorisation (Step 10), delegation between agents (Step 22, action
# "agent.delegate": tool = the worker, allowed_tools = the team) and inbound A2A calls (Step 24,
# action "a2a.inbound": tool = the published agent, allowed_tools = the published agents,
# subject = the remote caller's verified token). One decision per call; deny unless a rule allows.
#
# Input (agent_contracts.models.PolicyInput):
#   ctx                  {tenant_id, user_id, agent_id, risk_tier, run_id, ...}
#   tool, tool_ref       model-facing name and source ref (mcp://server/tool, local://...)
#   tool_tags            e.g. ["external"]
#   side_effect          "read" | "write" | "irreversible"
#   data_classification  "public" | "internal" | "confidential" | "restricted"
#   resource_tenant_id   tenant that owns the data, when the caller knows it
#   arguments            the call's arguments
#   subject              {authenticated, user, groups, roles, client_id}
#   allowed_tools        tools granted by the agent's spec (null when a person calls directly)
#
# Policy data (data.agent.config, policies/agent/config/data.json): kill switch, clearances,
# argument constraints.
#
# Output: data.agent.tools.decision = {effect, reasons, approvers?, version}
package agent.tools

import rego.v1

version := "2026-10-15.4"

cfg := data.agent.config

# ------------------------------------------------------------------ hard denials (any one wins)

deny contains "not authenticated" if not input.subject.authenticated

deny contains "tool execution is disabled for this tenant (kill switch)" if {
	cfg.kill_switch.tenants[input.ctx.tenant_id] == true
}

deny contains "tool execution is disabled for this agent (kill switch)" if {
	cfg.kill_switch.agents[input.ctx.agent_id] == true
}

deny contains sprintf("agent %s is disabled (kill switch)", [input.tool]) if {
	input.action in {"agent.delegate", "a2a.inbound"}
	cfg.kill_switch.agents[input.tool] == true
}

deny contains "cross-tenant access" if {
	input.resource_tenant_id != null
	input.resource_tenant_id != input.ctx.tenant_id
}

deny contains sprintf("tool %s is not granted to agent %s", [input.tool, input.ctx.agent_id]) if {
	input.allowed_tools != null
	not input.tool in input.allowed_tools
}

deny contains sprintf("%s data needs one of the groups %v", [input.data_classification, required]) if {
	required := cfg.clearance[input.data_classification]
	count(required) > 0
	not has_any_group(required)
}

deny contains sprintf("unknown side effect %q", [input.side_effect]) if {
	not input.side_effect in {"read", "write", "irreversible"}
}

deny contains msg if {
	some constraint in cfg.constraints[input.tool]
	value := object.get(input.arguments, constraint.argument, null)
	value != null
	value > constraint.max
	msg := sprintf("argument %s=%v exceeds the limit %v for risk tier %s", [constraint.argument, value, constraint.max, input.ctx.risk_tier])
	constraint.risk_tier == input.ctx.risk_tier
}

# ------------------------------------------------------------------ approvals

approval contains "irreversible action" if input.side_effect == "irreversible"

approval contains "external-facing write in a medium-risk agent" if {
	input.side_effect == "write"
	"external" in input.tool_tags
	input.ctx.risk_tier == "medium"
}

approval contains "write in a high-risk agent" if {
	input.side_effect == "write"
	input.ctx.risk_tier == "high"
}

approvers := "group:two-person-approvers" if {
	input.ctx.risk_tier == "high"
	input.side_effect == "irreversible"
} else := "group:approvers"

# A person's decision on this exact call (Step 15): approved, bound by the platform to this
# tool and these arguments, by someone other than the requester, who is in the policy's
# approver group. Two-person approval cannot be satisfied by one decision (Step 28).
approval_satisfied if {
	input.approval.approved == true
	input.approval.bound == true
	input.approval.approver != ""
	input.approval.approver != input.ctx.user_id
	approvers != "group:two-person-approvers"
	some g in input.approval.approver_groups
	endswith(g, sprintf("/%s", [trim_prefix(approvers, "group:")]))
}

# ------------------------------------------------------------------ decision

default decision := {"effect": "deny", "reasons": ["no rule allows this call"], "version": "2026-10-15.4"}

decision := {"effect": "deny", "reasons": sort(deny), "version": version} if count(deny) > 0

decision := {"effect": "require_approval", "reasons": sort(approval), "approvers": approvers, "version": version} if {
	count(deny) == 0
	count(approval) > 0
	not approval_satisfied
}

decision := {"effect": "allow", "reasons": [sprintf("approved by %s (%s)", [input.approval.approver, input.approval.approval_id])], "version": version} if {
	count(deny) == 0
	count(approval) > 0
	approval_satisfied
}

decision := {"effect": "allow", "reasons": [sprintf("%s tool allowed", [input.side_effect])], "version": version} if {
	count(deny) == 0
	count(approval) == 0
}

has_any_group(required) if {
	some g in input.subject.groups
	some r in required
	endswith(g, sprintf("/%s", [r]))
}

has_any_group(required) if {
	some role in input.subject.roles
	role in required
}
