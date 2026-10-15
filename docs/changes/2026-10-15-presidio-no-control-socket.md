# Stand-in: start Presidio without gunicorn's control socket

**Step:** 31 · **Commit:** fix(stack): start Presidio without gunicorn's control socket · **Date:** 2026-10-15

## What changed
The Presidio analyzer and anonymizer now start gunicorn directly with `--no-control-socket` (same worker count, bind address and app as the images' own entrypoint).

## Why
CI run 56 failed in `just standin-up`, before any test ran: `container p0-standin-presidio-anonymizer-1 is unhealthy`. Its gunicorn master started, but no worker ever logged "Booting worker".

Reproduced locally with the same image:
- The forked worker sat in `futex_do_wait` at 21 MB resident memory, and `/health` timed out.
- Both images ship gunicorn 25.1.0, whose master runs a control-socket thread. A child forked while that thread holds a lock never gets past the lock.
- With `--no-control-socket`, the anonymizer passed `/health` in 3 of 3 starts, each within 12 s. The default command had hung.
- With the fix, both services were healthy within about 24 s of a forced recreate, and the Presidio guardrail integration tests passed.

The analyzer failures in CI runs 49 and 50 showed the same symptom: master up, worker never ready. Step 30 put those down to slow spaCy model loading. The deadlock is the more likely cause. The analyzer's start period stays, because loading the model does take time.

## How to check it
`docker compose -f tests/stack/compose.p0-standin.yaml up -d --force-recreate presidio-analyzer presidio-anonymizer`, then both report healthy and the logs show "Booting worker". CI's integration job gets past `just standin-up`.

## Risks and follow-ups
The images' own entrypoint is bypassed. If a later Presidio image changes its app module or port, the command here must follow it. Revisit when the images move past gunicorn 25.1.0.
