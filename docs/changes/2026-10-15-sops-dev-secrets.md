# Keep dev secrets encrypted with SOPS and Project 0's age key

**Step:** 01 · **Commit:** chore(secrets): add SOPS config and dev secret encryption · **Date:** 2026-10-15

## What changed
`.sops.yaml` says which files are secrets (`secrets/*.enc.env|yaml|json`). The list of secret
*names* lives in `secrets/dev.env.example`. `just secrets-init` encrypts a filled-in
`secrets/dev.env` with the same age key Project 0 uses and deletes the plaintext. The key itself
is never in git: it is supplied through `SOPS_AGE_RECIPIENTS`.

## Why
Secrets in git are like house keys taped to the front door. SOPS locks them in a safe that only
holders of the age key can open, while the safe itself can be versioned with the code.

## How to check it
With a test key: `SOPS_AGE_RECIPIENTS=<age public key> just secrets-init` prints
`wrote secrets/dev.enc.env; plaintext removed`, and `sops -d secrets/dev.enc.env` shows the
values. `just secret-scan` fails if a `*.enc.*` file is not really encrypted.

## Risks and follow-ups
No encrypted dev file is committed yet: it must be created on the Mac with Project 0's real
age key (VERIFY-ON-MAC). Runtime tool secrets come from OpenBao (Step 29), never from SOPS.
