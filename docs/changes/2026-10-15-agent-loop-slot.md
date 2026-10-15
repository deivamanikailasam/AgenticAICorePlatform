# The agent loop becomes a switchable component

**Step:** 13 · **Commit:** feat(contracts): add the agent_loop slot · **Date:** 2026-10-15

## What changed
- A 25th slot, `agent_loop`: the engine that runs an agent's think-act loop for one agent node.
  Every engine is built from the platform's model client, tool executor and tool registry, so
  model access and tool policy stay the platform's whichever engine runs the loop.
- `AgentLoopContract`: use a tool and answer (with tool output reaching the model as untrusted),
  take the task from the input, return a structured answer, stop at `max_steps`, and never run an
  unknown tool. Two small test-double loops pass it, as does the native loop
  (`agent_loop.native`, the default).
- The `StopRun` signal moved into the contracts package so every engine can raise it.
- A spec chooses its engine with `components: { agent_loop: <name> }`; an unknown engine is a
  clear error.

## Why
Like a socket and plug standard: the platform provides the power (models, tools, policy) and any
engine that fits the socket can run the agent, with a test that says whether it fits.

## How to check it
`uv run pytest libs/agent-contracts libs/agent-runtime/tests/test_agent_loop.py`;
`uv run agent-components` lists 25 of 25 slots.

## Risks and follow-ups
Framework engines follow in the next commit.
