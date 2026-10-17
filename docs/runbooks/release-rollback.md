# AgentReleaseRolledBack

**Severity:** ticket · **Fires when:** the online-eval gate rolled a candidate (canary or shadow)
version of an agent back in the last 15 minutes.

## What it means
A new version was serving a share of production runs (canary), or running in their shadow, and
its scored runs were worse than the stable version's by more than its gate allows: failure rate,
cost per run or run time (Step 38). The platform has already stopped sending it traffic, on every
replica. Nobody needs to act to stop harm; someone needs to find out why it failed.

## Check
1. `GET /v1/admin/releases` (platform-operator role): the reason, and the numbers it was judged
   on (runs scored, failure rates, mean cost and run time for both versions).
2. The `release.rolled_back` audit event for the same numbers, and the candidate runs' traces in
   Tempo (filter `gen_ai.agent.name` and the version).
3. What changed between the two versions: `git diff` of `deploy/specs/<agent>/<stable>` and
   `.../<candidate>`, and the candidate's offline eval report from its promotion PR.

## Fix
- Fix the agent and release a **new version**. A rolled-back version never serves again.
- Open a pull request that removes the rolled-back candidate from `deploy/releases/prod.yaml`
  (`just promote <agent> --abort`), so Git matches what serves.
- If the gate itself was wrong (too few runs, a noisy metric), change it in the releases file
  through a reviewed pull request, with the evidence.

## Escalate
To the agent's owner (`owner` in its spec). If the stable version is also failing, see
[run-failures.md](run-failures.md).
