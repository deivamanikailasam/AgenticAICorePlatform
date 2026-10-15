# Component catalogue

Generated from the component registry by `just catalogue` — do not edit by hand. CI fails if
this file is stale. `implemented` means the component passes its contract tests; `stub` means a
documented adapter with a contract test and an ADR on when to complete it. The spec's
"Catalogue of switchable components" is the build target; this file is the truth.

| Slot | Name | Status | Implementation | Summary |
|---|---|---|---|---|
| approval_gate | memory | dummy | `agent_contracts.dummies.safety:InMemoryApprovalGate` | Approval requests in a dictionary. |
| approval_gate | ttl | dummy | `agent_contracts.dummies.safety:TtlApprovalGate` | Like ``memory`` but every request without an expiry gets a default one (SLA timer). |
| audit_sink | jsonl | dummy | `agent_contracts.dummies.observability:JsonlAuditSink` | Hash-chained records appended to a JSON Lines file (a temp file by default). |
| audit_sink | memory | dummy | `agent_contracts.dummies.observability:MemoryAuditSink` | Hash-chained records in a list. |
| budget_controller | ledger | dummy | `agent_contracts.dummies.safety:LedgerBudgetController` | Keeps every charge in a ledger (for audit) on top of the in-memory counters. |
| budget_controller | memory | dummy | `agent_contracts.dummies.safety:InMemoryBudgetController` | Counters per run; every charge also counts against all ancestors. |
| checkpointer | memory | dummy | `agent_contracts.dummies.state:InMemoryCheckpointer` | Checkpoints in a dictionary keyed by (tenant, thread). |
| checkpointer | sqlite | dummy | `agent_contracts.dummies.state:SqliteCheckpointer` | Checkpoints in SQLite (in-memory by default). The 'SQLite (tests)' catalogue option. |
| context_manager | summary_stub | dummy | `agent_contracts.dummies.context:SummaryStubContextManager` | Replaces dropped messages with one summary placeholder (real summaries: Step 21). |
| context_manager | trim | dummy | `agent_contracts.dummies.context:TrimContextManager` | Drops the oldest, lowest-priority messages until the context fits. |
| critic | length | dummy | `agent_contracts.dummies.reasoning:LengthCritic` | Passes outputs of at least ``min_words`` words. |
| critic | overlap | dummy | `agent_contracts.dummies.reasoning:OverlapCritic` | Scores the share of task words that the output mentions. |
| durable_executor | deferred | dummy | `agent_contracts.dummies.state:DeferredDurableExecutor` | Starts the workflow lazily, on the first signal or result request. No persistence. |
| durable_executor | inline | dummy | `agent_contracts.dummies.state:InlineDurableExecutor` | Starts the workflow as an asyncio task immediately. No persistence. |
| evaluator | exact_match | dummy | `agent_contracts.dummies.evals:ExactMatchEvaluator` | 1.0 when the output equals ``expected['output']`` (after trimming strings). |
| evaluator | tool_recall | dummy | `agent_contracts.dummies.evals:ToolRecallEvaluator` | Share of ``expected['tools']`` that the trajectory actually called. |
| guardrail | keyword_injection | dummy | `agent_contracts.dummies.safety:KeywordInjectionGuardrail` | Blocks text containing well-known injection phrases. A weak baseline, not a defence. |
| guardrail | regex_pii | dummy | `agent_contracts.dummies.safety:RegexPiiGuardrail` | Redacts e-mail addresses. Presidio replaces this in Step 27. |
| loop_detector | no_progress | dummy | `agent_contracts.dummies.safety:NoProgressLoopDetector` | Also flags ``patience`` consecutive steps that made no progress. |
| loop_detector | repeat | dummy | `agent_contracts.dummies.safety:RepeatLoopDetector` | Flags the same step signature seen ``threshold`` times within the last ``window`` steps. |
| memory_policy | read_only | dummy | `agent_contracts.dummies.context:ReadOnlyMemoryPolicy` | Memory can be read but never written by agents (the 'read-only memory' option). |
| memory_policy | validate_redact | dummy | `agent_contracts.dummies.context:ValidateRedactMemoryPolicy` | Refuses unvalidated untrusted writes; redacts e-mail addresses and phone numbers. |
| memory_store | memory | dummy | `agent_contracts.dummies.state:InMemoryMemoryStore` | Memory items in a dictionary; keyword-overlap search. |
| memory_store | sqlite | dummy | `agent_contracts.dummies.state:SqliteMemoryStore` | Memory items in SQLite (in-memory by default); keyword-overlap search in Python. |
| model_client | echo | dummy | `agent_contracts.dummies.models:EchoModelClient` | Echoes the last user message; satisfies response schemas with the smallest valid value. |
| model_client | litellm | implemented | `agent_runtime.models.gateway:GatewayModelClient` | Chat, tools, structured output and streaming through the Project 0 LiteLLM gateway. |
| model_client | scripted | dummy | `agent_contracts.dummies.models:ScriptedModelClient` | Replays a script of assistant turns (text or tool calls), then falls back to echo. |
| pattern_builder | chain | dummy | `agent_contracts.dummies.runtime:ChainPattern` | A straight chain of function nodes: ``{steps: [fn, fn, ...]}``. |
| pattern_builder | hybrid | implemented | `agent_compiler.patterns.graph:HybridPatternBuilder` | Workflow nodes and agent nodes mixed freely; agent nodes are expanded by the compiler. |
| pattern_builder | single | dummy | `agent_contracts.dummies.runtime:SingleNodePattern` | One function node: ``{fn: name}``. |
| pattern_builder | state_machine | implemented | `agent_compiler.patterns.graph:StateMachinePatternBuilder` | Same translation; states are nodes and transitions are edges with outcomes. |
| pattern_builder | workflow | implemented | `agent_compiler.patterns.graph:GraphPatternBuilder` | Translates ``graph: {entry, nodes, edges}`` one-to-one into plan nodes and edges. |
| planner | per_tool | dummy | `agent_contracts.dummies.reasoning:PerToolPlanner` | One step per available tool, chained in order; one step if there are no tools. |
| planner | single_step | dummy | `agent_contracts.dummies.reasoning:SingleStepPlanner` | A plan with exactly one step: the goal itself. |
| policy_decision_point | deny_all | dummy | `agent_contracts.dummies.safety:DenyAllPolicyDecisionPoint` | Denies everything: the safe default when no policy is configured. |
| policy_decision_point | rules | dummy | `agent_contracts.dummies.safety:RulesPolicyDecisionPoint` | In-process rules: same tenant only; irreversible needs approval. OPA: Step 10. |
| router | first | dummy | `agent_contracts.dummies.reasoning:FirstRouter` | Always the first route (a fixed path, useful as a baseline). |
| router | keyword | dummy | `agent_contracts.dummies.reasoning:KeywordRouter` | Picks the route whose name appears in the text; otherwise the first, with low confidence. |
| runtime_adapter | sequential | dummy | `agent_contracts.dummies.runtime:SequentialRuntimeAdapter` | Runs one node at a time in queue order. The Step 4 stub adapter. |
| runtime_adapter | stepwise | dummy | `agent_contracts.dummies.runtime:StepwiseRuntimeAdapter` | Runs every ready node in a 'superstep' before moving on (breadth-first). |
| simulator | patient_user | dummy | `agent_contracts.dummies.evals:PatientUserSimulator` | Repeats one goal until an assistant reply contains ``success_word`` or patience runs out. |
| simulator | scripted_user | dummy | `agent_contracts.dummies.evals:ScriptedUserSimulator` | Plays a fixed list of user turns, then says it is done. |
| telemetry_emitter | memory | dummy | `agent_contracts.dummies.observability:MemoryTelemetryEmitter` | Keeps finished spans and metrics in lists, for assertions in tests. |
| telemetry_emitter | noop | dummy | `agent_contracts.dummies.observability:NoopTelemetryEmitter` | Discards everything (telemetry switched off). |
| tool | add | dummy | `agent_contracts.dummies.tools:AddTool` | Adds two numbers. |
| tool | calculator | implemented | `agent_tools.builtin:CalculatorTool` | Evaluates an arithmetic expression safely (no code execution). |
| tool | clock | implemented | `agent_tools.builtin:ClockTool` | Returns the current date and time in a time zone (default Australia/Melbourne). |
| tool | echo | dummy | `agent_contracts.dummies.tools:EchoTool` | Returns its arguments unchanged. |
| tool | word_count | implemented | `agent_tools.builtin:WordCountTool` | Counts words and characters in a text. |
| tool_executor | local | dummy | `agent_contracts.dummies.tools:LocalToolExecutor` | Validates arguments, enforces timeouts and replays results for repeated idempotency keys. |
| tool_executor | platform | implemented | `agent_tools.executor:PlatformToolExecutor` | Validates, de-duplicates, times out, retries safely and spills large outputs to S3. |
| tool_executor | retrying | dummy | `agent_contracts.dummies.tools:RetryingToolExecutor` | Like ``local`` but honours each tool's retry policy for idempotent tools. |
| tool_registry | in_memory | implemented | `agent_tools.registry:InMemoryToolRegistry` | Tools in a dictionary. For unit tests and single-process tools. |
| tool_registry | indexed | dummy | `agent_contracts.dummies.tools:IndexedToolRegistry` | Keeps a per-name version index, sorted newest first. |
| tool_registry | memory | dummy | `agent_contracts.dummies.tools:InMemoryToolRegistry` | Dictionary keyed by (name, version). |
| tool_registry | postgres | implemented | `agent_tools.registry:PostgresToolRegistry` | Specs in ``agent_tools.tool_specs`` (Project 0 Postgres, ``agent`` DB); bindings in process. |
