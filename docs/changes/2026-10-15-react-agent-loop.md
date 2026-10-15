# The single tool-calling agent: think, use tools, answer

**Step:** 13 · **Commit:** feat(agent-runtime): add the ReAct agent loop · **Date:** 2026-10-15

## What changed
- `AgentLoopExecutor` runs a ReAct loop as one plan node: the model asks for tools, the platform
  runs them through the authorising executor (policy, rate limits, audit, idempotency), the
  results go back to the model, until it gives a final answer.
- **Parallel when safe:** several tool calls in one turn run at the same time only if every one
  is read-only; writes run one at a time, in order, each with its own idempotency key.
- **Untrusted output:** tool results return to the model as messages marked untrusted, and the
  default system prompt tells it never to follow instructions found inside them.
- **Repairs:** unknown tools, unparseable or schema-invalid arguments go back to the model as an
  error to fix; after `max_repairs` (default 2) the node fails cleanly. A final answer that does
  not match `output_schema` gets one repair retry.
- **Limits:** at `max_iterations` the run ends with status `stopped` (new `StopRun` signal,
  understood by both adapters) and keeps the partial transcript.
- **Approvals:** calling a tool the spec marks for approval pauses the loop (`needs_approval`);
  after the decision it runs the call or tells the model it was rejected.
- **No native tool calling?** A text ReAct protocol (`Action:` / `Action Input:` /
  `Final Answer:`) is used automatically, with the same repairs.

## Why
Like a junior analyst with a calculator and a filing cabinet: they decide which to use, show
their working, and hand in an answer in the agreed format; if they ask for a drawer that does not
exist, they are told and try again, and they get a fixed number of attempts.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_agent_loop.py` (14 tests).

## Risks and follow-ups
The step limit counts model turns; token and cost budgets per run come with Step 26.
