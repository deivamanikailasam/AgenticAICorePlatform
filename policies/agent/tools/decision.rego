# Tool-call decisions for the platform (Step 8 baseline; Step 10 adds tenant, data class,
# risk tier, rate limits and on-behalf-of rules).
#
# Input (see agent_contracts.models.PolicyInput):
#   input.action                 "tool.call"
#   input.ctx                    {tenant_id, user_id, agent_id, risk_tier, ...}
#   input.tool                   tool name, e.g. "rag-core__search"
#   input.side_effect            "read" | "write" | "irreversible"
#   input.data_classification    "public" | "internal" | "confidential" | "restricted"
#   input.resource_tenant_id     tenant that owns the data, if known
#   input.subject                {authenticated, groups, roles} (set by the gateway)
#
# Output: data.agent.tools.decision = {"effect": ..., "reasons": [...], "approvers": ...}
package agent.tools

import rego.v1

default decision := {"effect": "deny", "reasons": ["no rule allows this call"]}

decision := {"effect": "deny", "reasons": ["not authenticated"]} if not authenticated

decision := {"effect": "deny", "reasons": ["cross-tenant access"]} if {
	authenticated
	cross_tenant
}

decision := {"effect": "allow", "reasons": ["read tool, same tenant"]} if {
	authenticated
	not cross_tenant
	input.side_effect == "read"
}

decision := {"effect": "require_approval", "reasons": [sprintf("%s tool needs a human approval", [input.side_effect])], "approvers": "group:approvers"} if {
	authenticated
	not cross_tenant
	input.side_effect in {"write", "irreversible"}
}

authenticated if input.subject.authenticated == true

# null means "the caller does not know which tenant owns the data": only a known, different
# tenant is cross-tenant (Rego treats null as a value, not as false).
cross_tenant if {
	input.resource_tenant_id != null
	input.resource_tenant_id != input.ctx.tenant_id
}
