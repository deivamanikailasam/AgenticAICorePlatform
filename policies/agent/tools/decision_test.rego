package agent.tools_test

import rego.v1

import data.agent.tools

base := {
	"action": "tool.call",
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "invoice-triage", "risk_tier": "low"},
	"tool": "rag-core__search",
	"tool_tags": [],
	"side_effect": "read",
	"data_classification": "confidential",
	"resource_tenant_id": null,
	"arguments": {},
	"subject": {"authenticated": true, "groups": ["/acme/finance"], "roles": []},
	"allowed_tools": null,
}

with_input(patch) := object.union(base, patch)

# ---------------------------------------------------------------- allow

test_read_allowed if tools.decision.effect == "allow" with input as base

test_low_risk_write_allowed if {
	tools.decision.effect == "allow" with input as with_input({"side_effect": "write"})
}

test_granted_tool_allowed if {
	tools.decision.effect == "allow" with input as with_input({"allowed_tools": ["rag-core__search"]})
}

test_restricted_data_with_clearance_allowed if {
	d := tools.decision with input as with_input({
		"data_classification": "restricted",
		"subject": {"authenticated": true, "groups": ["/acme/restricted-readers"], "roles": []},
	})
	d.effect == "allow"
}

test_null_resource_tenant_is_not_cross_tenant if {
	tools.decision.effect == "allow" with input as with_input({"resource_tenant_id": null})
}

# ---------------------------------------------------------------- deny

test_anonymous_denied if {
	d := tools.decision with input as with_input({"subject": {"authenticated": false}})
	d.effect == "deny"
	"not authenticated" in d.reasons
}

test_cross_tenant_denied if {
	d := tools.decision with input as with_input({"resource_tenant_id": "globex"})
	d.effect == "deny"
	"cross-tenant access" in d.reasons
}

test_tool_not_granted_to_agent_denied if {
	d := tools.decision with input as with_input({"allowed_tools": ["platform__calculator"]})
	d.effect == "deny"
	d.reasons == ["tool rag-core__search is not granted to agent invoice-triage"]
}

test_restricted_data_without_clearance_denied if {
	d := tools.decision with input as with_input({"data_classification": "restricted"})
	d.effect == "deny"
}

test_tenant_kill_switch_denies_everything if {
	d := tools.decision with input as base with data.agent.config.kill_switch.tenants as {"acme": true}
	d.effect == "deny"
	d.reasons == ["tool execution is disabled for this tenant (kill switch)"]
}

test_agent_kill_switch_denies if {
	d := tools.decision with input as base with data.agent.config.kill_switch.agents as {"invoice-triage": true}
	d.effect == "deny"
}

test_payment_over_limit_denied_even_before_approval if {
	d := tools.decision with input as with_input({
		"tool": "erp__post_payment",
		"side_effect": "irreversible",
		"arguments": {"amount_aud": 25000},
		"ctx": object.union(base.ctx, {"risk_tier": "medium"}),
	})
	d.effect == "deny"
	d.reasons == ["argument amount_aud=25000 exceeds the limit 10000 for risk tier medium"]
}

test_unknown_side_effect_denied if {
	tools.decision.effect == "deny" with input as with_input({"side_effect": "teleport"})
}

test_all_denial_reasons_are_reported if {
	d := tools.decision with input as with_input({"resource_tenant_id": "globex", "allowed_tools": []})
	count(d.reasons) == 2
}

# ---------------------------------------------------------------- approval required

test_irreversible_needs_approval if {
	d := tools.decision with input as with_input({"side_effect": "irreversible"})
	d.effect == "require_approval"
	d.approvers == "group:approvers"
}

test_payment_within_limit_needs_approval if {
	d := tools.decision with input as with_input({
		"tool": "erp__post_payment",
		"side_effect": "irreversible",
		"arguments": {"amount_aud": 900},
		"ctx": object.union(base.ctx, {"risk_tier": "medium"}),
	})
	d.effect == "require_approval"
}

test_high_risk_irreversible_needs_two_people if {
	d := tools.decision with input as with_input({"side_effect": "irreversible", "ctx": object.union(base.ctx, {"risk_tier": "high"})})
	d.approvers == "group:two-person-approvers"
}

test_external_write_in_medium_risk_needs_approval if {
	d := tools.decision with input as with_input({"side_effect": "write", "tool_tags": ["external"], "ctx": object.union(base.ctx, {"risk_tier": "medium"})})
	d.effect == "require_approval"
}

test_any_write_in_high_risk_needs_approval if {
	d := tools.decision with input as with_input({"side_effect": "write", "ctx": object.union(base.ctx, {"risk_tier": "high"})})
	d.effect == "require_approval"
}

test_irreversible_is_never_plainly_allowed if {
	every tier in ["low", "medium", "high"] {
		d := tools.decision with input as with_input({"side_effect": "irreversible", "ctx": object.union(base.ctx, {"risk_tier": tier})})
		d.effect != "allow"
	}
}

test_decision_carries_policy_version if tools.decision.version == tools.version with input as base

# ---------------------------------------------------------------- approval evidence (Step 15)

approved_by(who, groups, bound) := {
	"approval_id": "run-1:pay.approval",
	"approved": true,
	"approver": who,
	"approver_groups": groups,
	"bound": bound,
}

payment(approval, risk) := with_input({
	"tool": "erp__post_payment",
	"side_effect": "irreversible",
	"arguments": {"amount_aud": 528},
	"ctx": object.union(base.ctx, {"risk_tier": risk}),
	"approval": approval,
})

test_bound_approval_from_an_approver_allows_the_payment if {
	d := tools.decision with input as payment(approved_by("j.smith", ["/acme/finance-approvers"], true), "medium")
	d.effect == "allow"
	d.reasons == ["approved by j.smith (run-1:pay.approval)"]
}

# Step 28: the tool's own approver group (policy data) applies, not just any approver
test_payment_needs_the_tools_own_approver_group if {
	d := tools.decision with input as payment(approved_by("j.smith", ["/acme/approvers"], true), "medium")
	d.effect == "require_approval"
	d.approvers == "group:finance-approvers"
}

test_approval_not_bound_to_these_arguments_still_needs_approval if {
	d := tools.decision with input as payment(approved_by("j.smith", ["/acme/approvers"], false), "medium")
	d.effect == "require_approval"
}

test_rejected_decision_still_needs_approval if {
	rejected := object.union(approved_by("j.smith", ["/acme/approvers"], true), {"approved": false})
	d := tools.decision with input as payment(rejected, "medium")
	d.effect == "require_approval"
}

test_requester_cannot_approve_their_own_payment if {
	d := tools.decision with input as payment(approved_by("alice", ["/acme/approvers"], true), "medium")
	d.effect == "require_approval"
}

test_approver_outside_the_group_does_not_count if {
	d := tools.decision with input as payment(approved_by("bob", ["/acme/finance"], true), "medium")
	d.effect == "require_approval"
}

test_one_approval_never_satisfies_two_person_approval if {
	d := tools.decision with input as payment(approved_by("j.smith", ["/acme/two-person-approvers"], true), "high")
	d.effect == "require_approval"
}

test_approval_never_overrides_a_denial if {
	over := object.union(payment(approved_by("j.smith", ["/acme/approvers"], true), "medium"), {"arguments": {"amount_aud": 20000}})
	d := tools.decision with input as over
	d.effect == "deny"
}

# ---------------------------------------------------------------- delegation (Step 22)

delegate := with_input({
	"action": "agent.delegate",
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "research-report", "risk_tier": "low"},
	"tool": "analyst",
	"tool_ref": "agent://analyst",
	"data_classification": null,
	"allowed_tools": ["researcher", "analyst", "writer"],
})

test_delegation_to_a_team_worker_allowed if tools.decision.effect == "allow" with input as delegate

test_delegation_outside_the_team_denied if {
	d := tools.decision with input as object.union(delegate, {"tool": "payments-agent"})
	d.effect == "deny"
	"tool payments-agent is not granted to agent research-report" in d.reasons
}

test_delegation_to_a_switched_off_worker_denied if {
	d := tools.decision with input as delegate with data.agent.config.kill_switch.agents as {"analyst": true}
	d.effect == "deny"
	"agent analyst is disabled (kill switch)" in d.reasons
}

# ---------------------------------------------------------------- inbound A2A (Step 24)

inbound := with_input({
	"action": "a2a.inbound",
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "a2a-client:partner-app", "risk_tier": "low"},
	"tool": "capital-desk",
	"tool_ref": "agent://capital-desk",
	"tool_tags": ["a2a"],
	"side_effect": "read",
	"data_classification": null,
	"subject": {"authenticated": true, "user": "alice", "client_id": "partner-app", "groups": ["/acme/support"], "roles": []},
	"allowed_tools": ["capital-desk"],
})

test_inbound_call_to_a_published_agent_allowed if tools.decision.effect == "allow" with input as inbound

test_inbound_call_to_an_unpublished_agent_denied if {
	d := tools.decision with input as object.union(inbound, {"tool": "invoice-triage"})
	d.effect == "deny"
	"tool invoice-triage is not granted to agent a2a-client:partner-app" in d.reasons
}

test_inbound_call_without_a_verified_caller_denied if {
	d := tools.decision with input as object.union(inbound, {"subject": {"authenticated": false}})
	d.effect == "deny"
	"not authenticated" in d.reasons
}

test_inbound_call_to_a_switched_off_agent_denied if {
	d := tools.decision with input as inbound with data.agent.config.kill_switch.agents as {"capital-desk": true}
	d.effect == "deny"
	"agent capital-desk is disabled (kill switch)" in d.reasons
}

# ---------------------------------------------------------------- two-person approval (Step 28)

two_person := with_input({
	"side_effect": "irreversible",
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "payments", "risk_tier": "high"},
	"approval": {
		"approved": true, "bound": true, "approval_id": "r:gate",
		"approver": "bob", "approver_groups": ["/acme/two-person-approvers"],
		"co_approver": "carol", "co_approver_groups": ["/acme/two-person-approvers"],
	},
})

test_two_different_approvers_allow_a_high_risk_irreversible_call if {
	tools.decision.effect == "allow" with input as two_person
}

test_one_approver_twice_is_not_two_people if {
	d := tools.decision with input as object.union(two_person, {"approval": object.union(two_person.approval, {"co_approver": "bob"})})
	d.effect == "require_approval"
}

test_the_requester_cannot_be_one_of_the_two if {
	d := tools.decision with input as object.union(two_person, {"approval": object.union(two_person.approval, {"co_approver": "alice"})})
	d.effect == "require_approval"
}

test_both_approvers_must_be_in_the_two_person_group if {
	d := tools.decision with input as object.union(two_person, {"approval": object.union(two_person.approval, {"co_approver_groups": ["/acme/finance"]})})
	d.effect == "require_approval"
}
