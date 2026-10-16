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
| A2A (Agent2Agent) | An open protocol for agents on different systems to send each other tasks and messages |
| Agent Card | A JSON document an A2A agent publishes: what it can do, where to reach it, how to authenticate |
| Signed Agent Card | An Agent Card carrying a digital signature, so a caller can check who published it |
| Pinned key | A public key written into configuration ahead of time; only signatures from it are accepted |
| Approval desk | The part of the platform that routes, times, shows and records human approvals |
| SLA (approval) | How long reviewers have to decide before the request escalates or expires |
| Escalation | Passing an undecided approval to a more senior group with a fresh deadline |
| Two-person rule | Two different people, neither of them the requester, must approve the riskiest actions |
| Edited approval | Approving a changed version of the proposed action; exactly the changed version runs |
| Review-after | A sample of finished runs checked by people after the fact |
| Take-over | A person stops the agent and finishes the work themselves |
| Direct prompt injection | An attack typed by the person, trying to make the agent break its rules |
| Indirect prompt injection | An attack hidden in content the agent reads: a document, web page, tool result or tool description |
| Quarantine (guardrails) | Replacing flagged untrusted content with a notice, so the model never reads it |
| Redaction | Replacing personal data or secrets in text with a placeholder such as `<EMAIL_ADDRESS>` |
| Fail closed | When a check cannot run, block rather than let content through unchecked |
| False positive | A harmless input that a check wrongly flags |
| Budget tree | Budgets nested like the runs: a worker's spending also counts against its team's budget |
| Trip | A limit being reached; the run stops and names the limit |
| Loop detection | Noticing an agent repeating itself, making no progress, or alternating between two steps |
| Circuit breaker | A switch that stops calling a dependency after repeated failures, then tries again after a pause |
| Half-open | A circuit breaker's trial state: one call is let through to see if the dependency recovered |
| Cost reconciliation | Checking the platform's cost records against the gateway's own accounting |
| Agent registry | The catalogue of every agent version: owner, risk, tools and lifecycle state |
| Lifecycle state | Where an agent version is in its life: draft, approved, deprecated or retired |
| Approval expiry | The date after which an approval no longer counts and the agent must be reviewed again |
| Spec hash | A fingerprint of an agent spec's content; a changed spec needs a new version |
| Backstage catalogue | A developer portal's list of software components, kept as YAML files in Git |
| Push notification (A2A) | The remote agent calling back a URL you gave it when a task changes state |
| Service identity | An agent's own login (a Keycloak client `agent-<name>`), separate from the user it acts for and from other agents |
| Delegation | Passing a user's request on with a new token that still names the user; here in two hops, platform → agent → service |
| Down-scoping | Each new token is valid for less: one audience, and only the scopes that client was given |
| Dynamic credential | A login created on demand for one use (here a Postgres user per tool call) instead of a stored password |
| Lease | OpenBao's record of a dynamic credential: its id and how long it lives; revoking the lease ends the credential |
| AppRole | An OpenBao login for machines: a role id plus a secret id; the runtime's own identity outside Kubernetes |
| JWT auth | An OpenBao login that accepts a signed token from an identity provider; agents log in with their Keycloak token |
| Workload identity | A machine proving what it is from where it runs (SPIFFE/SPIRE in Kubernetes) instead of holding a secret |
| Span | One timed step in a trace (an agent run, a model call, a tool call), with attributes describing it |
| Trace | All the spans of one piece of work, nested, sharing one trace id |
| Semantic conventions | Agreed span and metric names and attributes (here OpenTelemetry's GenAI and MCP ones), so any tool can read them |
| Langfuse | The tool that stores prompt and response content (redacted) for the people allowed to see it |
| Histogram | A metric that counts values into buckets, so percentiles (p50, p95) can be computed |
| p95 | The value 95% of cases stay under: "p95 run time 40 s" means 1 run in 20 takes longer |
| SLO | A service level objective: a target such as "99% of runs don't fail over 28 days" |
| Error budget | The share of failures an SLO allows (1% for 99%) |
| Burn rate | How fast the error budget is being used; 14.4× uses a month's budget in about two days |
| Runbook | The page an on-call person follows when an alert fires: what it means, check, fix, escalate |
| Audit trail | The permanent record of who did what, under which rules, and who allowed it; kept for years, unlike traces |
| Hash chain | Each record stores a fingerprint of itself plus the record before, so changing, removing or reordering one breaks every fingerprint after it |
| Append-only | Records can be added but never changed or deleted (enforced here by database triggers) |
| Digest | A fixed-length fingerprint (SHA-256) of some data: proves which exact data was used without storing it |
| Run reconstruction | Rebuilding a run's story from the audit trail plus its checkpoints, and checking the two agree |
| Agent card | A generated page saying what one agent is for, who owns it, how risky it is, what it may touch, its limits and its test results |
| System card | The same for a whole application: the lead agent, its workers, every tool and service, and where people approve |
| Lineage | The chain of what went into an agent: spec version, prompts, models, tools and datasets |
| AI-use register | The organisation's list of every AI system it runs, one line each: purpose, owner, risk, oversight |
| CODEOWNERS | A GitHub file naming who must review changes to which files |
| Required status check | A CI result GitHub insists is green before a pull request can merge (set in the repository's branch protection) |
| Testing pyramid | Many fast, exact tests at the bottom; fewer, slower, more realistic ones at the top |
| Fake (scripted model) | A stand-in that behaves exactly as the test scripts it to, so results are predictable |
| Contract test | One shared set of checks every implementation of an interface must pass |
| Cassette | A recording of the calls a run made and what came back, replayed in tests instead of the real services |
| Chaos test | A test that breaks a dependency on purpose (timeouts, outages, kills) to check the system recovers correctly |
| Circuit breaker | After repeated failures a dependency is skipped for a while, then tried again carefully |
| Fallback | Using a second option (here another model) when the first one fails |
| Golden task | A test case whose correct outcome is known in advance |
| Deterministic check | A pass/fail rule code can decide (the right tool was called, the amount matches) |
| Trajectory | The path an agent took: which tools and colleagues, in which order |
| LLM-as-judge | A model grading another model's output against written rules |
| Rubric | The written rules a grader applies, with examples of pass and fail |
| Calibration (judge) | Measuring how often the judge agrees with people on the same items |
| Cohen's kappa | Agreement beyond what chance alone would give: 1 is perfect, 0 is chance |
| Baseline | A committed result that later runs are compared against |
