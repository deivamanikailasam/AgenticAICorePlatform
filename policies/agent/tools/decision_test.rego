package agent.tools_test

import rego.v1

import data.agent.tools

base := {
	"action": "tool.call",
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "gateway"},
	"tool": "rag-core__search",
	"side_effect": "read",
	"subject": {"authenticated": true, "groups": ["finance"]},
}

test_read_allowed if tools.decision.effect == "allow" with input as base

test_anonymous_denied if {
	d := tools.decision with input as object.union(base, {"subject": {"authenticated": false}})
	d.effect == "deny"
	d.reasons == ["not authenticated"]
}

test_cross_tenant_denied if {
	d := tools.decision with input as object.union(base, {"resource_tenant_id": "globex"})
	d.effect == "deny"
	d.reasons == ["cross-tenant access"]
}

test_write_needs_approval if {
	d := tools.decision with input as object.union(base, {"side_effect": "write"})
	d.effect == "require_approval"
}

test_irreversible_never_plainly_allowed if {
	d := tools.decision with input as object.union(base, {"side_effect": "irreversible"})
	d.effect != "allow"
}

test_unknown_side_effect_denied if {
	d := tools.decision with input as object.union(base, {"side_effect": "teleport"})
	d.effect == "deny"
}

test_null_resource_tenant_is_not_cross_tenant if {
	tools.decision.effect == "allow" with input as object.union(base, {"resource_tenant_id": null})
}
