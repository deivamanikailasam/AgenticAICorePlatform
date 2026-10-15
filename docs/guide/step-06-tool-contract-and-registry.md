# Step 6 · Tool contract and registry

## 1. At a glance

| | |
|---|---|
| **Goal** | Every tool is typed, labelled, versioned and invoked through one safe door |
| **Time** | About a day |
| **You end up with** | `libs/agent-tools`: Postgres + in-memory registries, `PlatformToolExecutor`, Valkey idempotency, S3 spill, three built-in tools, tool cards, `just migrate` |
| **New words** | side-effect class, idempotency key, claim, spill, artefact, tool card, migration |

## 2. The big idea

A locked tool cage with a sign-out sheet. Every tool has a label (what it does, who owns it,
whether it only reads, changes something reversibly, or does something that can't be undone).
The cage door (the executor) checks every request before opening, and the sign-out sheet
(idempotency) makes sure the same request can't take the same tool twice.

> *Analogy:* a bank teller who checks the withdrawal slip is filled in correctly *before*
> opening the drawer, and who refuses to pay out a second time on the same slip number.

## 3. What we're doing

1. Extend `ToolSpec` with a model-safe `name` and a source `ref`.
2. Store specs in Postgres so every process sees the same tools and deprecations.
3. Route every call through `PlatformToolExecutor` with schema checks, idempotency, timeouts,
   safe retries, output checks and large-output spill.
4. Keep idempotency in Valkey and large outputs in S3 (Project 0 services).
5. Ship three safe built-ins and generate a card per tool.

## 4. Why we're doing this

The OWASP agentic list puts **tool misuse** (ASI02) near the top. Most failures are mundane:
malformed arguments, duplicated writes after a retry, a 5 MB tool result pasted into the
prompt. This step makes those impossible by construction, before policy (Step 10) decides *who*
may call *what*.

> *Watch out:* durability (Step 18) replays runs after a crash. Without idempotency keys, a
> replay could repeat a payment. Step 6 is what makes replay safe.

## 5. Key ideas

- **Side-effect class.** `read` (safe), `write` (reversible; needs a key), `irreversible`
  (needs a key and an approval; never retried automatically).
- **Idempotency key.** A unique id for one intended action. The first result is stored; repeats
  get the stored result.
- **Claim.** An atomic "I'm doing this" marker (`SET NX`) so two racing calls can't both run.
- **Spill.** Outputs above 64 KiB go to S3; the model sees a preview and an `artefact_uri`.
- **Tool card.** A generated one-page description of a tool for reviewers and auditors.
- **Migration.** A numbered SQL file applied once to create or change tables.

## 6. How to do it

```bash
uv run pytest libs/agent-tools                               # unit + contracts
just standin-up; set -a; . tests/stack/standin.env; set +a   # CI / cloud only
just provision && just migrate                               # bucket + agent_tools.tool_specs
uv run pytest -m integration libs/agent-tools                # Postgres, Valkey, S3
just cards                                                   # cards/tools/*.yaml, docs/cards/tools/*.md
```

On the Mac: `just with-secrets just provision && just with-secrets just migrate`.

To add your own tool, follow `docs/how-to/add-a-tool.md`.

## 7. Benefits

- Bad arguments never reach a tool; errors say exactly which field is wrong.
- Duplicated writes and replays are harmless; irreversible actions happen at most once per key.
- The context window is protected from huge tool outputs.
- Reviewers get a card per tool, generated from the code.

## 8. Drawbacks and risks

- Callers must supply idempotency keys for writes (the runtime does this from Step 11).
- Idempotency entries expire (24 h default); an action repeated after expiry runs again.
- The S3 preview may hide the part of a large output the model needed; tools should return
  focused results.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Postgres registry | In-memory | Single-process tests |
| Valkey idempotency | Postgres table | When Valkey isn't available; slower claims |
| S3 spill | Truncate only | Never for audit: the full output must be retrievable |
| JSON Schema checks | Pydantic models per tool | Python-only tools; MCP tools arrive as JSON Schema anyway |

## 10. Which approach fits which situation

- **Lookups and searches:** `read`, `idempotent: true`, a retry policy.
- **Drafts, tickets, comments:** `write`; the runtime supplies the key.
- **Payments, deletions, external emails:** `irreversible`; approval in the spec; no retries.

## 11. Common mistakes

- Marking a payment `write` "because it can be refunded": a refund is a new action, not an undo.
- Returning whole documents from a tool instead of the relevant part.
- Registering a tool in one process and expecting another to run it without `bind`.
- Editing an applied migration instead of adding a new one (`just migrate` refuses).

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| Schema-invalid arguments rejected before execution | ✅ | `test_invalid_arguments_rejected_before_execution` (contract), `test_argument_errors_say_where` |
| A duplicate idempotency key returns the first result | ✅ | contract test; `test_concurrent_duplicates_run_once`; Valkey `test_valkey_idempotency_across_executors` (stand-in) |
| Tool cards generated | ✅ | `cards/tools/*.yaml`, `docs/cards/tools/*.md`; `cards-check` in `just check` |
| Registry backed by Postgres, versioning and deprecation | ✅ stand-in · **VERIFY-ON-MAC** | `TestPostgresRegistryContract` |
| Output size cap and spill to S3 | ✅ stand-in · **VERIFY-ON-MAC** | `test_large_outputs_spill…`, `test_large_output_spills_to_s3` |
| ADR | ✅ | ADR 0016 |

## 13. Recap

Step 6 built the tool cage: typed, versioned tool specs shared through Postgres; one executor
door that validates arguments first, demands idempotency keys for writes, replays duplicates,
never retries irreversible actions, checks outputs and spills large ones to S3; Valkey-backed
claims so racing duplicates run once; three safe built-in tools; and generated tool cards.
