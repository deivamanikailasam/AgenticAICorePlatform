package agent.approvals_test

import data.agent.approvals
import rego.v1

base := {
	"tool": "send_email",
	"side_effect": "write",
	"risk_tier": "medium",
	"requested_approvers": null,
	"ctx": {"tenant_id": "acme", "user_id": "alice", "agent_id": "helpdesk"},
}

test_default_group_and_sla if {
	r := approvals.route with input as base
	r.approvers == "group:approvers"
	r.sla_minutes == 60
	r.escalate_to == "group:approval-leads"
	not r.two_person
}

test_spec_group_is_used if {
	r := approvals.route with input as object.union(base, {"requested_approvers": "group:support-leads"})
	r.approvers == "group:support-leads"
}

test_tool_override_beats_spec_group if {
	r := approvals.route with input as object.union(base, {"tool": "erp__post_payment", "requested_approvers": "group:support-leads"})
	r.approvers == "group:finance-approvers"
}

test_high_risk_irreversible_needs_two_people if {
	r := approvals.route with input as object.union(base, {"side_effect": "irreversible", "risk_tier": "high", "tool": "erp__post_payment"})
	r.approvers == "group:two-person-approvers"
	r.two_person
	r.sla_minutes == 30
	"high-risk irreversible action: two people must approve" in r.reasons
}

test_low_risk_gets_longer_sla if {
	r := approvals.route with input as object.union(base, {"risk_tier": "low"})
	r.sla_minutes == 240
}
