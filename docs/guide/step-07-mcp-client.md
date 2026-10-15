# Step 7 · MCP client

## 1. At a glance

| | |
|---|---|
| **Goal** | Use external MCP tool servers — local (stdio) and remote (HTTP) — safely |
| **Time** | About a day |
| **You end up with** | `McpClientPool` (allowlist, negotiation, discovery, trust, OAuth, tracing, audit), the `mcp.servers` allowlist in `platform.yaml`, a compile-time check, a Keycloak token verifier |
| **New words** | MCP, stdio, Streamable HTTP, protocol negotiation, annotation, protected resource metadata, client credentials |

## 2. The big idea

MCP is a universal plug for tools. But you wouldn't plug any device you found on the street into
your office wiring. Step 7 adds a fuse box: only approved sockets (the allowlist), the right
voltage (protocol negotiation), a label check (annotations trusted only from known makers), a key
for remote sockets (OAuth), and a meter on every circuit (traces and audit).

> *Analogy:* a hotel concierge who will only book restaurants on the hotel's approved list, checks
> the restaurant speaks a language they share, and logs every booking.

## 3. What we're doing

1. Add `mcp.servers` to `platform.yaml` (transport, protocol, trust, allowed tools, side effects, auth).
2. Build `McpClientPool` on the official SDK (2.3.x).
3. Negotiate 2026-07-28, fall back to the 2025-11-25 handshake.
4. Discover tools and expose them as `mcp://<server>/<tool>` / `<server>__<tool>`.
5. Authenticate to remote servers with OAuth client credentials from Keycloak.
6. Trace and audit every call; refuse unlisted servers at runtime and compile time.

## 4. Why we're doing this

Most new agent capabilities arrive as MCP servers. Each is third-party code or a third-party
service that can lie about what its tools do, inject instructions in tool descriptions, or
quietly exfiltrate data. The OWASP agentic list covers this under tool misuse (ASI02) and
supply chain (ASI04).

> *Watch out:* a tool *description* is text the model reads. A malicious server can hide
> "ignore previous instructions" there. Descriptions from untrusted servers are clipped here and
> scanned by guardrails in Step 27.

## 5. Key ideas

- **stdio vs Streamable HTTP.** A local server runs as a child process talking over stdin/stdout;
  a remote one is an HTTPS endpoint.
- **Protocol negotiation.** The client and server agree a protocol version: 2026-07-28 (stateless,
  each request self-contained) or the older 2025-11-25 handshake.
- **Annotations.** Hints like `readOnlyHint` or `destructiveHint`. Useful, but only believed from
  trusted servers.
- **Protected resource metadata.** A remote server says which authorization server issues its
  tokens; the client fetches a token from there (OAuth client credentials).
- **Namespacing.** Two servers can both have a `search` tool; `crm__search` and `wiki__search`
  keep them apart.

## 6. How to do it

```bash
uv run pytest libs/agent-tools/tests/test_mcp.py                 # offline
just standin-up; set -a; . tests/stack/standin.env; set +a       # CI / cloud only
just provision                                                    # Keycloak client agent-platform
uv run pytest -m integration libs/agent-tools/tests/test_mcp_integration.py
```

In code:

```python
pool = McpClientPool(load_platform(), audit=audit_sink)
await pool.register_all(tool_registry, "filesystem")  # discover + register
result = await executor.execute(
    ToolCall(name="filesystem__read_text_file", arguments={"path": "/data/notes.txt"}), ctx
)
```

To add a server, follow `docs/how-to/add-an-mcp-server.md`.

## 7. Benefits

- One allowlist, enforced at compile time and runtime.
- Works with both current and older MCP servers.
- Remote servers use short-lived OAuth tokens, never long-lived shared keys.
- Every call is visible in traces and in the audit chain, without storing sensitive arguments.

## 8. Drawbacks and risks

- Curating the allowlist and side-effect maps is ongoing work for platform owners.
- Clipping descriptions can hide useful detail from long but honest servers.
- `npx`-launched servers download code at first run: pin versions and review them.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Official SDK | Hand-written JSON-RPC | Never; the protocol moves quickly |
| Allowlist | Open discovery | Never in production |
| OAuth client credentials | Static bearer token | Short-lived experiments with a server that can't do OAuth |
| Direct client | Everything via the platform gateway | Step 8 adds the gateway so all traffic shares one policy point |

## 10. Which approach fits which situation

- **Vendor or internal server under review:** `trusted: true`, still with `allowed_tools`.
- **Community server:** `trusted: false`, explicit `side_effects`, minimal `allowed_tools`, pinned version.
- **Older server that can't handle probes:** `protocol: legacy` (or let `auto` fall back).

## 11. Common mistakes

- `@latest` in a stdio command: an unreviewed update runs on your machine.
- Marking a server trusted because its tools *look* harmless.
- Forgetting that stdio servers get no secrets from the environment: pass what they need via `env`
  (and keep secrets out of it).

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| Two external servers (one local, one remote) are discovered, called, traced and audited | ✅ stand-in · **VERIFY-ON-MAC** | `test_two_external_servers_discovered_called_traced_audited`: official filesystem server (stdio, negotiated 2025-11-25) and an OAuth-protected HTTP server (2026-07-28 via Keycloak) |
| An unlisted server is refused | ✅ | `test_unlisted_server_is_refused`, `test_unlisted_server_refused`, compiler `test_unlisted_mcp_servers_are_refused_at_compile_time` |
| 2026-07-28 with negotiation down to 2025-11-25 | ✅ | `test_negotiates_modern_and_falls_back_to_handshake` |
| OAuth for remote servers | ✅ stand-in | the remote server returns 401 without a token; succeeds with a Keycloak client-credentials token |
| Annotations untrusted unless the server is trusted | ✅ | `test_discovery_namespaces_and_trusts_only_trusted_annotations` |
| Audited into Project 0's audit log | ⚠️ Step 31 | events go to an `AuditSink` now; the Project 0 audit adapter is Step 31 |
| ADR | ✅ | ADR 0017 |

## 13. Recap

Step 7 made external MCP servers safe to use: an allowlist in `platform.yaml` enforced by the
client and the compiler, automatic negotiation between the 2026-07-28 and 2025-11-25 protocol
versions, namespaced discovery, annotations trusted only from trusted servers, OAuth client
credentials for remote servers, stdio servers isolated from the platform's secrets, and a trace
span and audit event for every call.
