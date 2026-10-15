# Component catalogue

Generated from the component registry by `just catalogue` — do not edit by hand. CI fails if
this file is stale. `implemented` means the component passes its contract tests; `stub` means a
documented adapter with a contract test and an ADR on when to complete it. The spec's
"Catalogue of switchable components" is the build target; this file is the truth.

| Slot | Name | Status | Implementation | Summary |
|---|---|---|---|---|
| agent_loop | google_adk | stub | `agent_runtime.engines.stubs:GoogleAdkLoopStub` | Google ADK. STUB in-process: every ADK release up to 2.12.0 (2026-10-13) pins |
| agent_loop | minimal | dummy | `agent_contracts.dummies.agent_loop:MinimalReactLoop` | Model → tools (one at a time) → model until an answer; unknown tools end the loop. |
| agent_loop | native | implemented | `agent_runtime.agent_loop:AgentLoopExecutor` | ``NodeKind.AGENT`` nodes whose config has ``loop: react``. |
| agent_loop | openai_agents | implemented | `agent_runtime.engines.openai_agents:OpenAIAgentsLoopExecutor` |  |
| agent_loop | pydantic_ai | implemented | `agent_runtime.engines.pydantic_ai:PydanticAILoopExecutor` |  |
| agent_loop | single_shot | dummy | `agent_contracts.dummies.agent_loop:SingleShotToolLoop` | Same loop with a step limit of at most three model turns (a cheap, impatient agent). |
| agent_registry | backstage | implemented | `agent_registry.backstage:BackstageCatalogRegistry` |  |
| agent_registry | memory | implemented | `agent_contracts.dummies.registry:InMemoryAgentRegistry` | Records in a dictionary keyed by (name, version). |
| agent_registry | postgres | implemented | `agent_registry.postgres:PostgresAgentRegistry` |  |
| agent_registry | sqlite | implemented | `agent_contracts.dummies.registry:SqliteAgentRegistry` | The same behaviour, persisted to a SQLite file (local development: ``just agents`` |
| approval_gate | memory | dummy | `agent_contracts.dummies.safety:InMemoryApprovalGate` | Approval requests in a dictionary. |
| approval_gate | postgres | implemented | `agent_runtime.approval_store:PostgresApprovalGate` |  |
| approval_gate | ttl | dummy | `agent_contracts.dummies.safety:TtlApprovalGate` | Like ``memory`` but every request without an expiry gets a default one (SLA timer). |
| audit_sink | jsonl | dummy | `agent_contracts.dummies.observability:JsonlAuditSink` | Hash-chained records appended to a JSON Lines file (a temp file by default). |
| audit_sink | memory | dummy | `agent_contracts.dummies.observability:MemoryAuditSink` | Hash-chained records in a list. |
| budget_controller | ledger | dummy | `agent_contracts.dummies.safety:LedgerBudgetController` | Keeps every charge in a ledger (for audit) on top of the in-memory counters. |
| budget_controller | memory | dummy | `agent_contracts.dummies.safety:InMemoryBudgetController` | Counters per run; every charge also counts against all ancestors. |
| budget_controller | platform | implemented | `agent_runtime.budget:PlatformBudgetController` |  |
| checkpointer | memory | dummy | `agent_contracts.dummies.state:InMemoryCheckpointer` | Checkpoints in a dictionary keyed by (tenant, thread). |
| checkpointer | postgres | implemented | `agent_runtime.checkpoints:PostgresCheckpointer` | ``agent_runtime.checkpoints`` in the ``agent`` database (Project 0 Postgres). |
| checkpointer | sqlite | dummy | `agent_contracts.dummies.state:SqliteCheckpointer` | Checkpoints in SQLite (in-memory by default). The 'SQLite (tests)' catalogue option. |
| checkpointer | valkey | implemented | `agent_runtime.checkpoints:ValkeyCheckpointer` | Development only: checkpoints in Valkey (one list per thread). No retention guarantees. |
| code_sandbox | firecracker | implemented | `sandbox_runner.engines:FirecrackerSandbox` | Firecracker microVMs: each run boots its own Linux kernel under KVM (ADR 0019, revised). |
| code_sandbox | gvisor | implemented | `sandbox_runner.engines:GvisorSandbox` | gVisor (runsc): a user-space kernel between the code and the host kernel. Default. |
| code_sandbox | kata | implemented | `sandbox_runner.engines:KataSandbox` | Kata Containers: each run in a lightweight VM (needs KVM on the host). |
| code_sandbox | managed | stub | `sandbox_runner.engines:ManagedSandbox` | A managed sandbox service (vendor API). Documented STUB by decision (ADR 0021): sending |
| code_sandbox | scripted | dummy | `agent_contracts.dummies.sandbox:ScriptedSandbox` | Returns a scripted result without executing anything (for testing tools and patterns). |
| code_sandbox | unavailable | dummy | `agent_contracts.dummies.sandbox:UnavailableSandbox` | Refuses every request: the safe default when no isolation engine is installed. |
| context_manager | offload_s3 | implemented | `agent_runtime.context:OffloadContextManager` | ``context_manager.offload_s3``: archive the oldest turns and long tool results to the |
| context_manager | rolling_summary | implemented | `agent_runtime.context:RollingSummaryContextManager` | ``context_manager.rolling_summary`` (default): summarise the oldest turns, chunk by chunk. |
| context_manager | summary_stub | dummy | `agent_contracts.dummies.context:SummaryStubContextManager` | Replaces dropped messages with one summary placeholder (a test double; real rolling |
| context_manager | trim | implemented | `agent_runtime.context:TrimContextManager` | ``context_manager.trim``: drop the oldest turns; no summary. |
| context_manager | trim_dummy | dummy | `agent_contracts.dummies.context:TrimContextManager` | Drops the oldest, lowest-priority messages until the context fits (a test double; the |
| credential_broker | memory | dummy | `agent_contracts.dummies.credentials:InMemoryCredentialBroker` | Issues a fresh random user name and password for each allowed source. |
| credential_broker | openbao | implemented | `agent_tools.openbao:OpenBaoCredentialBroker` | Dynamic credentials from ``<mount>/creds/<role>``; leases revoked through |
| credential_broker | static | dummy | `agent_contracts.dummies.credentials:StaticCredentialBroker` | Issues the same test values every time (for tests that check exact values). |
| credential_broker | vault | implemented | `agent_tools.openbao:VaultCredentialBroker` | HashiCorp Vault: the same HTTP API. Verified against OpenBao, not against Vault |
| critic | length | dummy | `agent_contracts.dummies.reasoning:LengthCritic` | Passes outputs of at least ``min_words`` words. |
| critic | llm | implemented | `agent_runtime.reasoning:LlmCritic` |  |
| critic | overlap | dummy | `agent_contracts.dummies.reasoning:OverlapCritic` | Scores the share of task words that the output mentions. |
| durable_executor | dbos | stub | `agent_runtime.temporal.stubs:DbosDurableExecutor` | DBOS. STUB: durable steps in Postgres from a library; finish when Temporal's operational |
| durable_executor | deferred | dummy | `agent_contracts.dummies.state:DeferredDurableExecutor` | Starts the workflow lazily, on the first signal or result request. No persistence. |
| durable_executor | inline | dummy | `agent_contracts.dummies.state:InlineDurableExecutor` | Starts the workflow as an asyncio task immediately. No persistence. |
| durable_executor | restate | stub | `agent_runtime.temporal.stubs:RestateDurableExecutor` | Restate. STUB: needs the Restate server and its SDK's handler model; finish when a team |
| durable_executor | temporal | implemented | `agent_runtime.temporal.runtime:TemporalDurableExecutor` | ``durable_executor.temporal``: registered async functions as Temporal workflows. |
| embedder | gateway | implemented | `agent_runtime.models.embeddings:GatewayEmbedder` |  |
| embedder | hashing | dummy | `agent_contracts.dummies.embedder:HashingEmbedder` | Feature hashing of words and character trigrams into ``dimensions`` buckets, L2-normalised. |
| embedder | word_hashing | dummy | `agent_contracts.dummies.embedder:WordHashingEmbedder` | Words only (no character trigrams): coarser, so spelling variants do not match. |
| evaluator | exact_match | dummy | `agent_contracts.dummies.evals:ExactMatchEvaluator` | 1.0 when the output equals ``expected['output']`` (after trimming strings). |
| evaluator | tool_recall | dummy | `agent_contracts.dummies.evals:ToolRecallEvaluator` | Share of ``expected['tools']`` that the trajectory actually called. |
| guardrail | keyword_injection | dummy | `agent_contracts.dummies.safety:KeywordInjectionGuardrail` | Blocks text containing well-known injection phrases. A weak baseline, not a defence. |
| guardrail | output_schema | implemented | `agent_guardrails.checks:OutputGuardrail` | The answer against a JSON Schema (``schema``, or the spec's output schema passed in |
| guardrail | pii_redaction | implemented | `agent_guardrails.pii:PresidioPiiGuardrail` |  |
| guardrail | pii_redaction.cloud_dlp | implemented | `agent_guardrails.pii:CloudDlpPiiGuardrail` | Documented stub (ADR 0038): needs a cloud account and a data-residency decision. |
| guardrail | pii_redaction.presidio | implemented | `agent_guardrails.pii:PresidioPiiGuardrail` |  |
| guardrail | prompt_injection | implemented | `agent_guardrails.injection:PromptInjectionGuardrail` | ``prompt_injection`` in specs: the platform's detectors together (rules always). |
| guardrail | prompt_injection.classifier | implemented | `agent_guardrails.injection:ClassifierInjectionGuardrail` |  |
| guardrail | prompt_injection.llm_judge | implemented | `agent_guardrails.injection:LlmJudgeInjectionGuardrail` |  |
| guardrail | prompt_injection.rules | implemented | `agent_guardrails.injection:RulesInjectionGuardrail` |  |
| guardrail | regex_pii | dummy | `agent_contracts.dummies.safety:RegexPiiGuardrail` | Redacts e-mail addresses and phone numbers. Presidio replaces this in Step 27. |
| guardrail | safety_filter | implemented | `agent_guardrails.checks:SafetyFilterGuardrail` | Rules for clearly harmful requests and answers (weapons, self-harm methods, malware, |
| guardrail | secret_leak | implemented | `agent_guardrails.secrets:SecretLeakGuardrail` |  |
| guardrail | tool_args_schema | implemented | `agent_guardrails.checks:ToolArgsGuardrail` | Tool arguments against the tool's JSON Schema and per-argument limits from the spec:: |
| guardrail | topic_filter | implemented | `agent_guardrails.checks:TopicFilterGuardrail` | Keeps an agent to its job: ``blocked`` topics (name -> phrases or regexes) are refused in |
| loop_detector | no_progress | dummy | `agent_contracts.dummies.safety:NoProgressLoopDetector` | Also flags ``patience`` consecutive steps that made no progress. |
| loop_detector | platform | implemented | `agent_runtime.budget:PlatformLoopDetector` |  |
| loop_detector | repeat | dummy | `agent_contracts.dummies.safety:RepeatLoopDetector` | Flags the same step signature seen ``threshold`` times within the last ``window`` steps. |
| memory_policy | human_curated | implemented | `agent_memory.policy:HumanCuratedMemoryPolicy` | Only items a person curated (``source="human:<id>"``, validated) are stored; every other |
| memory_policy | read_only | dummy | `agent_contracts.dummies.context:ReadOnlyMemoryPolicy` | Memory can be read but never written by agents (the 'read-only memory' option). |
| memory_policy | validate_redact | dummy | `agent_contracts.dummies.context:ValidateRedactMemoryPolicy` | Refuses unvalidated untrusted writes; redacts e-mail addresses and phone numbers. |
| memory_policy | validating | implemented | `agent_memory.policy:ValidatingMemoryPolicy` |  |
| memory_store | letta | stub | `agent_memory.stubs:LettaMemoryStore` | Letta. STUB: the self-hosted V1 API server (archival memory) is retired. |
| memory_store | mem0 | implemented | `agent_memory.mem0:Mem0MemoryStore` |  |
| memory_store | memory | dummy | `agent_contracts.dummies.state:InMemoryMemoryStore` | Memory items in a dictionary; keyword-overlap search. |
| memory_store | pgvector | implemented | `agent_memory.pgvector:PgVectorMemoryStore` |  |
| memory_store | qdrant | implemented | `agent_memory.qdrant:QdrantMemoryStore` |  |
| memory_store | sqlite | dummy | `agent_contracts.dummies.state:SqliteMemoryStore` | Memory items in SQLite (in-memory by default); keyword-overlap search in Python. |
| memory_store | zep | stub | `agent_memory.stubs:ZepMemoryStore` | Zep. STUB: Community Edition is unsupported; Zep Cloud needs an account and API key. |
| model_client | echo | dummy | `agent_contracts.dummies.models:EchoModelClient` | Echoes the last user message; satisfies response schemas with the smallest valid value. |
| model_client | litellm | implemented | `agent_runtime.models.gateway:GatewayModelClient` | Chat, tools, structured output and streaming through the Project 0 LiteLLM gateway. |
| model_client | scripted | dummy | `agent_contracts.dummies.models:ScriptedModelClient` | Replays a script of assistant turns (text or tool calls), then falls back to echo. |
| pattern_builder | agentic_rag | implemented | `agent_compiler.patterns.react:AgenticRagPatternBuilder` | ``pattern: agentic_rag`` (Step 16): plan hops, retrieve through rag-core, cite, self-check. |
| pattern_builder | chain | dummy | `agent_contracts.dummies.runtime:ChainPattern` | A straight chain of function nodes: ``{steps: [fn, fn, ...]}``. |
| pattern_builder | debate | implemented | `agent_compiler.patterns.team:DebatePatternBuilder` |  |
| pattern_builder | evaluator_optimiser | implemented | `agent_compiler.patterns.react:EvaluatorOptimiserPatternBuilder` | Optimiser drafts, separate evaluator scores, keep the best; bounded rounds. |
| pattern_builder | group_chat | implemented | `agent_compiler.patterns.team:GroupChatPatternBuilder` |  |
| pattern_builder | handoff | implemented | `agent_compiler.patterns.team:HandoffPatternBuilder` |  |
| pattern_builder | hierarchical | implemented | `agent_compiler.patterns.team:HierarchicalPatternBuilder` | Same node: workers that are supervisors themselves form the hierarchy. |
| pattern_builder | hybrid | implemented | `agent_compiler.patterns.graph:HybridPatternBuilder` | Workflow nodes and agent nodes mixed freely; agent nodes are expanded by the compiler. |
| pattern_builder | map_reduce | implemented | `agent_compiler.patterns.team:MapReducePatternBuilder` |  |
| pattern_builder | plan_execute | implemented | `agent_compiler.patterns.react:PlanExecutePatternBuilder` | Plan, carry out each step with a small ReAct run, re-plan on failure. |
| pattern_builder | prompt_chain | implemented | `agent_compiler.patterns.graph:PromptChainPatternBuilder` | ``pattern: prompt_chain`` (Step 15): a straight line of steps (LLM, function, tool), each |
| pattern_builder | react | implemented | `agent_compiler.patterns.react:ReactPatternBuilder` |  |
| pattern_builder | reflection | implemented | `agent_compiler.patterns.react:ReflectionPatternBuilder` | Draft, self-critique, revise; bounded rounds. |
| pattern_builder | rewoo | implemented | `agent_compiler.patterns.react:RewooPatternBuilder` | Plan once with tool arguments, run the tools without re-prompting, solve once. |
| pattern_builder | router | implemented | `agent_compiler.patterns.graph:RouterPatternBuilder` | ``pattern: router`` (Step 15): the entry is a router node and every route it may choose |
| pattern_builder | single | dummy | `agent_contracts.dummies.runtime:SingleNodePattern` | One function node: ``{fn: name}``. |
| pattern_builder | state_machine | implemented | `agent_compiler.patterns.graph:StateMachinePatternBuilder` | Same translation; states are nodes and transitions are edges with outcomes. |
| pattern_builder | supervisor | implemented | `agent_compiler.patterns.team:SupervisorPatternBuilder` |  |
| pattern_builder | workflow | implemented | `agent_compiler.patterns.graph:GraphPatternBuilder` | Translates ``graph: {entry, nodes, edges}`` one-to-one into plan nodes and edges. |
| planner | llm | implemented | `agent_runtime.reasoning:LlmPlanner` |  |
| planner | per_tool | dummy | `agent_contracts.dummies.reasoning:PerToolPlanner` | One step per available tool, chained in order; one step if there are no tools. |
| planner | single_step | dummy | `agent_contracts.dummies.reasoning:SingleStepPlanner` | A plan with exactly one step: the goal itself. |
| policy_decision_point | cerbos | implemented | `agent_tools.policy_cerbos:CerbosPolicyDecisionPoint` | Cerbos PDP over its HTTP API (``POST /api/check/resources``). |
| policy_decision_point | composite | implemented | `agent_tools.relationships:CompositePolicyDecisionPoint` | ``policy_decision_point.composite``: primary PDP, then OpenFGA relationships. |
| policy_decision_point | deny_all | dummy | `agent_contracts.dummies.safety:DenyAllPolicyDecisionPoint` | Denies everything: the safe default when no policy is configured. |
| policy_decision_point | opa | implemented | `agent_tools.policy:OpaPolicyDecisionPoint` | Evaluates ``data.<package path>`` (default ``agent/tools/decision``) over HTTP. |
| policy_decision_point | rules | dummy | `agent_contracts.dummies.safety:RulesPolicyDecisionPoint` | In-process rules: same tenant only; irreversible needs approval, satisfied by a bound |
| router | embedding | implemented | `agent_runtime.routing:EmbeddingRouter` |  |
| router | first | dummy | `agent_contracts.dummies.reasoning:FirstRouter` | Always the first route (a fixed path, useful as a baseline). |
| router | keyword | dummy | `agent_contracts.dummies.reasoning:KeywordRouter` | Picks the route whose name appears in the text; otherwise the first, with low confidence. |
| router | llm | implemented | `agent_runtime.routing:LlmRouter` |  |
| router | rules | implemented | `agent_runtime.routing:RulesRouter` |  |
| runtime_adapter | agent_framework | implemented | `agent_runtime.adapters.agent_framework:AgentFrameworkAdapter` |  |
| runtime_adapter | crewai | stub | `agent_runtime.adapters.stubs:CrewAIAdapter` | CrewAI. STUB (catalogue): role-based crews do not map onto the platform's plan graph |
| runtime_adapter | langgraph | implemented | `agent_runtime.adapters.langgraph:LangGraphAdapter` |  |
| runtime_adapter | sequential | dummy | `agent_contracts.dummies.runtime:SequentialRuntimeAdapter` | Runs one node at a time in queue order. The Step 4 stub adapter. |
| runtime_adapter | stepwise | dummy | `agent_contracts.dummies.runtime:StepwiseRuntimeAdapter` | Runs every ready node in a 'superstep' before moving on (breadth-first). |
| runtime_adapter | temporal | implemented | `agent_runtime.temporal.runtime:TemporalRuntime` |  |
| simulator | patient_user | dummy | `agent_contracts.dummies.evals:PatientUserSimulator` | Repeats one goal until an assistant reply contains ``success_word`` or patience runs out. |
| simulator | scripted_user | dummy | `agent_contracts.dummies.evals:ScriptedUserSimulator` | Plays a fixed list of user turns, then says it is done. |
| telemetry_emitter | memory | dummy | `agent_contracts.dummies.observability:MemoryTelemetryEmitter` | Keeps finished spans and metrics in lists, for assertions in tests. |
| telemetry_emitter | noop | dummy | `agent_contracts.dummies.observability:NoopTelemetryEmitter` | Discards everything (telemetry switched off). |
| tool | add | dummy | `agent_contracts.dummies.tools:AddTool` | Adds two numbers. |
| tool | calculator | implemented | `agent_tools.builtin:CalculatorTool` | Evaluates an arithmetic expression safely (no code execution). |
| tool | clock | implemented | `agent_tools.builtin:ClockTool` | Returns the current date and time in a time zone (default Australia/Melbourne). |
| tool | echo | dummy | `agent_contracts.dummies.tools:EchoTool` | Returns its arguments unchanged. |
| tool | rag_core_search | implemented | `agent_tools.ragcore:RagCoreSearchTool` | Search company documents through Project 0's rag-core, with the caller's own permissions. |
| tool | sandbox_python | implemented | `agent_tools.sandbox_tool:SandboxPythonTool` | Run a short Python program in an isolated sandbox (no network) and return its output. |
| tool | sql_read | implemented | `agent_tools.sql_read:SqlReadTool` |  |
| tool | word_count | implemented | `agent_tools.builtin:WordCountTool` | Counts words and characters in a text. |
| tool_executor | authorising | implemented | `agent_tools.authorising:AuthorisingToolExecutor` | AuthorisingToolExecutor(inner: 'ToolExecutor', registry: 'ToolRegistry', pdp: 'PolicyDecisionPoint', audit: 'AuditSink', limiter: 'RateLimiter' = <factory>, grants: 'Mapping[str, ToolGrant] | None' = None, default_rate_limit_per_minute: 'int | None' = None, broker: 'CredentialBroker | None' = None) |
| tool_executor | local | dummy | `agent_contracts.dummies.tools:LocalToolExecutor` | Validates arguments, enforces timeouts and replays results for repeated idempotency keys. |
| tool_executor | platform | implemented | `agent_tools.executor:PlatformToolExecutor` | Validates, de-duplicates, times out, retries safely and spills large outputs to S3. |
| tool_executor | retrying | dummy | `agent_contracts.dummies.tools:RetryingToolExecutor` | Like ``local`` but honours each tool's retry policy for idempotent tools. |
| tool_registry | in_memory | implemented | `agent_tools.registry:InMemoryToolRegistry` | Tools in a dictionary. For unit tests and single-process tools. |
| tool_registry | indexed | dummy | `agent_contracts.dummies.tools:IndexedToolRegistry` | Keeps a per-name version index, sorted newest first. |
| tool_registry | memory | dummy | `agent_contracts.dummies.tools:InMemoryToolRegistry` | Dictionary keyed by (name, version). |
| tool_registry | postgres | implemented | `agent_tools.registry:PostgresToolRegistry` | Specs in ``agent_tools.tool_specs`` (Project 0 Postgres, ``agent`` DB); bindings in process. |
