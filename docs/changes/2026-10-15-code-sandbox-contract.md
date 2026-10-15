# Add a code-sandbox socket and the tests every sandbox must pass

**Step:** 09 · **Commit:** feat(contracts): add the CodeSandbox interface, models and contract · **Date:** 2026-10-15

## What changed
The catalogue's "code sandbox" slot becomes the 24th interface, `CodeSandbox`, with request,
limits and result models (`SandboxRequest`, `SandboxLimits`, `SandboxResult`). Network is
`none` by type: model-written code can never ask for a network. The shared contract checks that
a real (kernel-isolated) engine runs hello-world, reports failures as a status, blocks the
network, cuts runaway loops at the time limit, moves files in and out, and caps output; test
doubles must never execute code at all. Two doubles are registered: `unavailable` (refuses) and
`scripted` (canned results for testing tools).

## Why
Agents that write code are useful (analysis, data wrangling) and dangerous (OWASP ASI05:
unexpected code execution). A socket with a strict safety test means any sandbox technology can
be plugged in, but only one that contains hostile code passes.

## How to check it
`just components --slot code_sandbox`; `uv run pytest libs/agent-contracts`.

## Risks and follow-ups
Real engines arrive in the next commit.
