# Runbooks for the kill switch and the reference applications

## What changed
- `docs/runbooks/kill-switch.md`: the levers that stop an agent, a tool or a tenant, narrowest
  first. These are the policy kill switch (agent or tenant), the feature flags, aborting a
  candidate, cancelling a run and rejecting an approval. The page covers what each one stops and
  when it takes effect, how to check that it holds, and how to turn it back on.
- `docs/runbooks/reference-apps.md`: how to operate the knowledge assistant, the research
  report and invoice triage: their approvals, budgets, owners and usual problems.
- The runbook index lists both.

## Why
Step 40 asks for the final runbooks. Until now, the kill switch was documented only inside the
Step 10 chapter and the production guide.

## How to check it
Each claim names the file or test it rests on:
- the ASI10 red-team attacks, `asi10-rogue-agents.yaml`;
- `test_feature_flags_switch_an_agent_or_a_tool_off`;
- `test_promotion.py`;
- the policy's denial reasons in `policies/agent/tools/decision.rego`.

## Risks and follow-ups
- In staging and prod, the kill switch reaches OPA only when the signed policy bundle is
  republished. How fast that happens depends on the operator's bundle server.
- There is no dashboard panel for policy denials yet.
