# Golden tool tasks for the single tool-calling agent (Step 13)

`cases.jsonl` — one task per line: `id`, `question`, `tool` (the tool a correct agent uses),
`arguments` (what it should be called with) and `expect` (a string the final answer must
contain).

Two runs use this file:

- **CI (mechanics):** `libs/agent-compiler/tests/test_react_agent.py` drives the real agent loop,
  real platform tools and the authorising executor with a deterministic stand-in model that
  issues each case's call. It proves the loop, tools, policy and answer plumbing, not model
  quality.
- **Mac (model quality, VERIFY-ON-MAC):** `uv run pytest -m mac
  libs/agent-compiler/tests/test_react_agent.py` runs the same cases against Project 0's local
  model through the gateway and prints how many were solved. Only that run says anything about
  how well a model uses tools; report it with the dataset size (currently 8 cases).
