# Approval routing (Step 28): who must approve a paused action, how long they have, and who it
# goes to if they don't decide in time. Queried as data.agent.approvals.route.
#
# Input:
#   tool, side_effect ("read" | "write" | "irreversible"), risk_tier ("low" | "medium" | "high")
#   requested_approvers  the group the spec names (null if none)
#   ctx                  {tenant_id, user_id, agent_id, ...}
#
# Data (data.agent.config.approvals): default_group, two_person_group, escalate_to,
# sla_minutes per risk tier, tools.<tool>.approvers overrides.
#
# Output: {approvers, two_person, sla_minutes, escalate_to, reasons, version}
package agent.approvals

import rego.v1

version := "2026-10-15.1"

cfg := data.agent.config.approvals

two_person if {
	input.risk_tier == "high"
	input.side_effect == "irreversible"
}

# precedence: two-person rule > the tool's own approvers > the spec's group > the default
approvers := cfg.two_person_group if two_person

else := cfg.tools[input.tool].approvers if cfg.tools[input.tool].approvers

else := input.requested_approvers if {
	is_string(input.requested_approvers)
	input.requested_approvers != ""
}

else := cfg.default_group

sla_minutes := object.get(cfg.sla_minutes, input.risk_tier, 60)

reasons contains "high-risk irreversible action: two people must approve" if two_person

reasons contains sprintf("tool %s is approved by %s", [input.tool, cfg.tools[input.tool].approvers]) if {
	not two_person
	cfg.tools[input.tool].approvers
}

reasons contains sprintf("decide within %d minutes (%s risk)", [sla_minutes, input.risk_tier])

route := {
	"approvers": approvers,
	"two_person": two_person,
	"sla_minutes": sla_minutes,
	"escalate_to": cfg.escalate_to,
	"reasons": sort(reasons),
	"version": version,
}

default two_person := false
