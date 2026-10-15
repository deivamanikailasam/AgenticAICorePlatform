# Add an MCP server allowlist to platform.yaml

**Step:** 07 · **Commit:** feat(spec): add the MCP server allowlist to platform.yaml · **Date:** 2026-10-15

## What changed
`platform.yaml` gains `mcp.servers`, the only MCP servers the platform will talk to. Each entry
says how to reach the server (`stdio` command or `http` URL), which protocol mode to use
(`auto`, `legacy`, `2026-07-28`), whether its tool annotations are trusted, which tools are
exposed, owner-set side effects per tool, how to authenticate (`none`, `bearer`, or OAuth client
credentials with secrets given as `${secret:…}` references), and which environment variables a
stdio server may see (proxy and CA settings by default — never the whole environment). The
official filesystem server is listed as the first example.

## Why
An allowlist is the guest list at the door: an agent can't plug in a random tool server it read
about in a document. Annotations from an untrusted server are like a stranger's name badge —
useful, but not proof.

## How to check it
`uv run python -c "from agent_spec.platform import load_platform; print([s.name for s in load_platform().mcp.servers])"`
prints `['filesystem']`.

## Risks and follow-ups
The allowlist is enforced by the client (this step), the compiler (next commit) and the gateway
(Step 8).
