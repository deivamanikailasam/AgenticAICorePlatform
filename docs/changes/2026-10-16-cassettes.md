# Record-and-replay cassettes for model and tool calls

**Step:** 33 · **Commit:** feat(agent-testing): record-and-replay cassettes for model and tool calls · **Date:** 2026-10-16

## What changed
New library `libs/agent-testing`:
- **`Cassette`.** A JSON file of the model and tool calls a run made: each request, with per-run metadata such as the run id left out, and its response.
- **Recording.** `RecordingModelClient` and `RecordingTool` wrap the real model and tools and write every call, including tool errors and streamed chunks.
- **Replaying.** `ReplayModelClient` and `ReplayTool` answer from the cassette and never touch the real system. Identical requests are answered in recorded order, so parallel calls replay correctly.
- **Mismatches.** A request the cassette doesn't hold raises `CassetteMismatchError`, which quotes where it differs from the closest recording.
- **`transcript()`.** The canonical bytes of the calls made. A replay is byte-identical to its recording when the transcripts are equal.
- **Re-recording.** `just record-cassettes` rewrites the committed cassette, `services/agent-api/tests/cassettes/counter.json`.

## Why
Step 33 wants deterministic integration tests: real model and tool behaviour, recorded once and replayed in CI with no network.

Only the edges are recorded. Policy, guardrails, budgets and approvals run live during replay, so replayed tests still test the platform.

## How to check it
- `uv run pytest libs/agent-testing services/agent-api/tests/test_cassettes.py`:
  - A run through the run API (ReAct agent, two parallel tool calls, a final answer) is recorded, then replayed twice with no model or tools. Both replays are byte-identical to the recording, with the same output.
  - The committed cassette replays offline.
  - A changed input fails the run with "no recorded model call matches this request".
  - Tool errors and streams replay exactly.
- Re-recording produced a byte-identical file.
- With the stand-in (`-m integration`), a run recorded over HTTP against LiteLLM replays with every socket connection refused, byte-identical.

## Risks and follow-ups
- A cassette pins a behaviour. When a prompt or tool changes on purpose, re-record and review the cassette diff like code.
- A cassette holds whatever the recorded run saw. Record only against stand-in or synthetic data, never production.
