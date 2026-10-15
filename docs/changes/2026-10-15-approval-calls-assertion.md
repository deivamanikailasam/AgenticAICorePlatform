# Test: the approval event names exactly the call that ran

**Step:** 31 · **Commit:** test(agent-api): approval events name the exact call that ran · **Date:** 2026-10-15

## What changed
`test_an_approved_payment_leaves_a_complete_versioned_trail` now asserts that the `approval.decided` event's `calls` equals `[{"tool": "pay", "arguments_digest": <the pay call's digest>}]`. The earlier assertion fell back to the call's own digest, so it could never fail.

## Why
An assertion that always passes proves nothing. Approval events have carried `calls` since the reconstruction commit.

## How to check it
`uv run pytest services/agent-api/tests/test_audit_trail.py`

## Risks and follow-ups
None.
