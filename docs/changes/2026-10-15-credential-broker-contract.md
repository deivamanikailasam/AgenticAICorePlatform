# Credential broker contract

**Step:** 29 · **Commit:** feat(agent-contracts): credential broker interface, contract and dummies · **Date:** 2026-10-15

## What changed
- New slot `credential_broker`. Its interface, `CredentialBroker`, has three methods:
  - `issue(source, ctx)` gives one tool call a short-lived credential;
  - `revoke(credential)` ends the credential at once;
  - `valid(credential)` says whether the issuer still honours it.
- New model `Credential`:
  - The secret values are `SecretStr`, left out of `repr` and of every dump.
  - `lease()` returns the part an audit record keeps: lease id, source, who it was issued to, and expiry.
- Shared contract `CredentialBrokerContract`. A broker passes if:
  - credentials are distinct and expire within 15 minutes;
  - secret values never appear in `repr`, `str`, JSON dumps or the lease;
  - a revoked credential is no longer valid, and revoking twice is fine;
  - unknown and forbidden sources are refused.
- Two dummies pass the contract: `credential_broker.memory` (generated values) and `credential_broker.static` (fixed test values).

## Why
Step 29 says tools get short-lived credentials at call time, credentials never enter model context, and a leaked lease expires within minutes. The contract makes those properties testable for every implementation: OpenBao now, cloud secret managers later.

## How to check it
`uv run pytest libs/agent-contracts`

## Risks and follow-ups
`MAX_TTL_SECONDS` is 900 (15 minutes), the upper end of "within minutes". The provisioned tool role uses 5 minutes.
