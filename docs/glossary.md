# Glossary

Every term used in this repository, in plain English. Grows with each step.

| Term | Plain-English meaning |
|---|---|
| Agent | An AI model that decides its next step and uses tools to act, inside limits |
| Workflow | A fixed sequence of steps decided by code, possibly with AI steps inside |
| Autonomy level | How much the model decides versus the code |
| ADR | Architecture decision record: one page explaining a hard-to-reverse choice and the options rejected |
| Workspace (uv) | One repository holding several Python packages developed together, with one lock file |
| Lock file | A file that records the exact version of every dependency so installs are identical |
| Idempotent | Doing it twice has the same effect as doing it once |
| External network | A Docker network owned by another project that our containers join |
| Virtual key | A gateway key with its own model allowlist and budget, used to attribute cost |
| SOPS | A tool that encrypts the values in a config file so it can be committed safely |
| age | A small, modern encryption tool; SOPS uses it to lock secrets |
| Stand-in stack | Upstream images started only in CI or cloud sessions to imitate Project 0's services |
| Digest pinning | Referring to a container image by its content hash so it can never change underneath you |
| Tool | A function the agent can call, with a typed input and output |
| Side-effect class | Whether a tool only reads, changes something reversibly, or does something that can't be undone |
| MCP | Model Context Protocol: the standard plug for connecting agents to tools and data |
| A2A | Agent-to-Agent protocol: the standard way agents from different systems talk |
| AG-UI | Agent-User Interaction protocol: the standard event stream from an agent to a user interface |
| Agent Card | A published description of an A2A agent: skills, endpoint, auth |
| Checkpoint | A saved snapshot of a run's state so it can resume after a crash or pause |
| Durable execution | Running long processes so they survive failures and waits without repeating finished work |
| Idempotency key | A unique ID that makes repeating a request safe: the second attempt returns the first result |
| Human-in-the-loop (HITL) | A person approves, edits or rejects an agent's action before it happens |
| Guardrail | An automatic check before or after a model or tool call |
| Prompt injection | Hidden instructions in text that try to make the model ignore its rules; *indirect* when they arrive through documents or tool results |
| Trajectory | The sequence of steps and tool calls an agent took to reach its answer |
| Context engineering | Deciding exactly what goes into the model's limited context window |
| Supervisor | An agent that splits work and delegates to worker agents |
| Handoff | One agent passing control and context to another |
| Kill switch | A tested way to stop a run, an agent version or a tenant's agents immediately |
| AIBOM | AI bill of materials: inventory of agents, models, tools and MCP servers |
| Canary | Sending a small share of traffic to a new version before everyone |
| ReAct | Reason–act–observe loop: the model calls a tool, reads the result, decides again |
| Plan-and-execute | The model writes a plan first, then each step is carried out; re-plans when a step fails |
| ReWOO | "Reasoning without observation": plan once with tool arguments, run the tools, answer once |
| Reflection | The model critiques its own draft and revises it, for a bounded number of rounds |
| Evaluator-optimiser | One model drafts, a separate evaluator scores, the drafter improves; best candidate wins |
| Router | A step that picks one of a fixed set of next steps for a request (by an LLM, by similarity to examples, or by rules) |
| Embedding | A list of numbers representing a text's meaning, so similar texts can be found by comparing numbers |
| Validation gate | A check on a step's output before the next step uses it; failures are sent back for a retry |
| Approval evidence | A person's decision attached to one exact action (tool and arguments), checked by policy before the action runs |
| Multi-hop retrieval | Answering by chaining searches, where a later search uses what an earlier one found |
| Grounding | Every claim in an answer is supported by the passages it cites |
| Naive RAG | One search, then one answer from what came back |
| AG-UI | An open event protocol between agents and user interfaces (text, tool calls, state, interrupts) |
| SSE | Server-sent events: a long HTTP response the server keeps writing events into |
| Idempotency key | A client-chosen key that makes repeating the same request safe: the second one returns the first one's result |
| Checkpoint | A saved snapshot of a run after a step, so it can continue from there |
| Time travel | Looking at a run's earlier checkpoints, and starting a new branch from one of them |
| Retention | How long saved data is kept before it is deleted |
| Workflow (Temporal) | Code that only decides what happens next; the server records every decision, so the run can be rebuilt anywhere after a crash |
| Activity | One piece of real work in a workflow (a model or tool call), retried by the server if it fails |
| Signal | A message sent into a running workflow, such as an approval |
| Durable timer | A wait recorded on the server, not in a process's memory, so it keeps counting through restarts |
| Heartbeat | A regular "still alive" message from a long activity, so a dead worker is noticed quickly |
| Semantic memory | Facts the agent keeps between conversations, such as a user's stated preference |
| Episodic memory | Records of past runs and how they ended |
| Procedural memory | Learned instructions, versioned like prompts; only from the platform or a user who confirmed them |
| Memory poisoning | Getting false facts or instructions into an agent's memory so they steer later runs |
| Taint | The mark a run gets once untrusted content is in its context; writes after that count as untrusted |
| Right to erasure | A person's right to have the personal information an organisation holds about them deleted |
| Context window | The most text (in tokens) a model can take in one call |
| Rolling summary | A running summary of the oldest part of a conversation, extended as more is folded in |
| Tool-result compaction | Shortening an old tool result to its beginning and end, with a pointer to the full text |
| Just-in-time tools | Showing a model only the tools it likely needs, and letting it search for more |
| Prompt caching | A provider reusing the unchanged start of a prompt from an earlier call, which is faster and cheaper |
| Supervisor | An agent that hands parts of a task to worker agents and decides when the task is done |
| Worker | An agent in a team with its own tools, permissions and budget |
| Shared state | The typed fields a team builds up together; each worker reads and writes only its own |
| Circuit breaker | Stopping use of something that keeps failing, so one failure does not cascade |
| Delegation | One agent handing work to another; decided by policy and audited |
| Handoff (swarm) | One agent passing control of the conversation, with a note, to a specialist agent |
| Group chat | Several agents taking turns on one shared conversation until a stop condition |
| Map-reduce | Doing the same step for many items in parallel (map), then combining the results (reduce) |
| Debate | Agents arguing different sides while a judge decides |
| Termination condition | What makes a pattern stop, by itself or because a limit was reached |
