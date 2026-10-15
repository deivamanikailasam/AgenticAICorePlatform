# Step 10 · Tool authorisation

## 1. At a glance

| | |
|---|---|
| **Goal** | Every tool call is judged by one rulebook, acts with the user's own rights, is rate-limited and leaves a trace |
| **Time** | About two days |
| **You end up with** | The full `agent.tools` Rego policy and its data, the `AuthorisingToolExecutor`, Valkey rate limits, on-behalf-of MCP calls, the Cerbos policy engine and OpenFGA relationship checks as switchable options |
| **New words** | policy decision point (PDP), attribute rule, relationship rule, kill switch, clearance, token exchange, audience, rate window, fail closed |

## 2. The big idea

A security desk in a building lobby. Everyone who wants to use a tool passes the same desk. The
guard checks the badge (who you are), the floor you want (which tool), the department you belong
to (tenant), what you plan to do (read, change, or something that cannot be undone), and how
risky the visit is. The answer is "go ahead", "no", or "go ahead once a manager signs". The guard
writes every answer in the log book, and turns people away if they come through too often.

> *Analogy:* a bank teller's rulebook. The same withdrawal can be fine, refused, or "needs a
> manager", depending on who asks, from which account, how much, and how risky the branch is.

## 3. What we're doing

1. Write the full tool-call policy in Rego, with its changeable values in a data file.
2. Put one authorising executor in front of every tool: policy, rate limit, run, audit.
3. Make external MCP tools act on behalf of the user through token exchange.
4. Add Cerbos as an alternative policy engine that must decide exactly like OPA.
5. Add OpenFGA for relationship questions ("is alice a payer of this invoice?").
6. Prove that a call for another tenant's data is denied and audited.

## 4. Why we're doing this

The most common agent security failure is a tool that runs with a powerful service account
(OWASP ASI02 tool misuse, ASI03 privilege abuse). If the model is tricked into calling a tool, the
damage is limited to what the *user* could do anyway, and the policy still says no to anything
outside the agent's grant, the user's tenant or the user's clearance.

> *Watch out:* a policy that is only checked "in most places" is not a policy. That is why there
> is exactly one executor that every caller must use.

## 5. Key ideas

- **Inputs to every decision.** The user (authenticated, groups, roles), the tenant, the agent
  and its risk tier, the tool (name, source ref, tags such as `external`), its side-effect class,
  the data classification, the arguments, and the tools the agent's spec grants.
- **Three answers.** `allow`, `deny`, `require_approval` — always with reasons and the policy
  version, so an auditor can see *why*.
- **Hard denials win.** Not signed in; kill switch for the tenant or agent; another tenant's
  data; a tool the spec does not grant; data above the user's clearance; an unknown side effect;
  an argument above its limit for the risk tier.
- **Approvals.** Every irreversible action; external-facing writes in medium-risk agents; every
  write in high-risk agents. High-risk irreversible actions need two-person approvers.
- **Rules versus data.** Rules are code (`decision.rego`); kill switches, clearances and limits
  are data (`policies/agent/config/data.json`). Operators change data without touching rules.
- **The spec only tightens.** `approval:` in agent.yaml can add an approval; it can never undo a
  deny.
- **Tenant of the data.** Tools that act on tenant-owned data take a `tenant_id` argument; it is
  compared with the caller's tenant.
- **On behalf of.** The platform swaps the user's token for a new one that only one downstream
  service accepts (OAuth token exchange, RFC 8693). No user token, no call.
- **Rate window.** A one-minute counter per tenant, agent and tool in Valkey, shared by every
  replica.
- **Attributes versus relationships.** "Finance users may pay invoices" is an attribute rule
  (OPA or Cerbos). "Only the team that owns *this* invoice may pay it" is a relationship
  (OpenFGA).
- **Fail closed.** If any engine is down or answers nonsense, the answer is deny.

## 6. How to do it

Run the policy tests (OPA and Cerbos):

```bash
just policy-test        # Rego: PASS 21/21 · Cerbos: 9 tests executed [9 OK]
```

Change a value, not a rule — for example, turn off tools for one tenant:

```json
"kill_switch": { "tenants": { "acme": true }, "agents": {} }
```

then `just with-secrets just provision-policies` (and mirror it in `policies/cerbos/tool.yaml` if
you use Cerbos; a test fails if they differ).

Grant tools, limits and approvals in an agent spec (the executor takes these as grants; the
runtime builds them from the spec from Step 11 on):

```yaml
tools:
  - ref: mcp://erp/get_invoice
    rate_limit_per_minute: 30
  - ref: mcp://erp/post_payment
    approval: { required: true, approvers: "group:finance-approvers" }
```

Let an external MCP server see the user, not the platform (`platform.yaml`):

```yaml
mcp:
  servers:
    - name: crm
      transport: http
      url: https://crm.internal/mcp
      auth:
        kind: on_behalf_of
        audience: crm                      # also add it to infra/provision/keycloak.yaml
        issuer: http://keycloak:8080/realms/rag-platform
        client_id: agent-platform
        client_secret: ${secret:keycloak/agent-platform#client_secret}   # for tools/list only
        scope: agent-platform:crm
```

Switch the policy engine of the gateway (optional):

```bash
just up-alternatives                     # Cerbos and OpenFGA (compose profiles)
just with-secrets just provision-openfga # store + model; second run: "model: up to date"
MCP_GATEWAY_PDP=cerbos+openfga just up
```

## 7. Benefits

- One place to read, test and audit who may do what with which tool.
- A prompt-injected agent still cannot reach another tenant, exceed a limit or skip an approval.
- Downstream services see the real user and apply their own permissions too.
- Organisations that already use Cerbos or OpenFGA can plug them in, with tests proving equal
  behaviour.

## 8. Drawbacks and risks

- One extra network round trip per call (two with OpenFGA).
- Two copies of the rules when Cerbos is used; the parity test is the guard, so extend it with
  every new rule.
- The `tenant_id` convention only protects tools that follow it; tools must be reviewed.
- The fixed rate window can let up to twice the limit through across a minute boundary.
- The argument limits in `data.json` are examples for the invoice reference app, not business
  advice; set real values per deployment.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| OPA (Rego) | Cerbos (YAML + CEL) | Your teams already run Cerbos or prefer YAML policies; implemented, parity-tested |
| Rego data for groups | OpenFGA relationships | Per-object sharing (owners, teams, delegations) at scale; implemented |
| Fixed one-minute window | Token bucket or sliding window | Billing-grade or very smooth limits |
| Token exchange per audience | Forwarding the user's token | Never: it hands a broad token to every service |
| Per-call MCP connection for on-behalf-of | Per-user connection pool | Latency-sensitive, high-volume tools |

## 10. Which approach fits which situation

- **Default (Mac and most deployments):** OPA from Project 0, no extra services.
- **Company standard is Cerbos:** `MCP_GATEWAY_PDP=cerbos`; keep the parity test green.
- **Objects shared by owner or team (invoices, cases, documents):** add `+openfga`, list the
  tools in `relations.yaml`, and have the owning application write the relationships.
- **High-risk agents:** risk tier `high`; every write then needs approval and irreversible
  actions need two people.
- **Incident:** flip the kill switch for the tenant or agent in `data.json` and reload.

## 11. Common mistakes

- Putting `if user.is_admin` inside a tool instead of in the policy.
- Using the platform's service account "temporarily" for a downstream call.
- Leaving a tenant-scoped tool without a `tenant_id` argument.
- Changing `data.json` but not the Cerbos constants (the constants test fails — read it).
- Treating "OPA is down" as "allow": the platform denies, by design.
- Starting OpenFGA without `OPENFGA_PRESHARED_KEY_DEV`: it refuses to start, by design.

## 12. Done when

| Check | Status | Evidence (this build, stand-in) |
|---|---|---|
| Rego unit tests cover allow, deny and approval-required | ✅ | `just policy-test`: `PASS: 21/21` |
| A tool called for another tenant's data is denied and audited | ✅ | `test_another_tenants_data_is_denied_and_audited`: alice (acme) asks for globex → `cross-tenant access`, one `policy.decision` deny event with alice/acme, the same-tenant call runs |
| Decisions use user, tenant, agent, tool, side effect, data class, arguments, risk tier | ✅ | Rego tests for each input; `test_authorising.py` shows them sent |
| Tools act with the user's rights | ✅ | `test_on_behalf_of_server_sees_the_user_and_never_the_platform`: server sees `alice`; anonymous call refused |
| Per-tool rate limits in Valkey | ✅ | `test_valkey_limiter_shares_the_count_between_instances`; executor returns `rate_limited` and audits it |
| Cerbos alternative | ✅ | Cerbos 0.56.0: contract + 19 parity cases equal to OPA |
| OpenFGA alternative | ✅ | OpenFGA v1.22.0: owner-team viewer allowed, bob denied, same id in another tenant denied, payer still needs approval |
| On the Mac | ⚠️ **VERIFY-ON-MAC** | Project 0's OPA loads `data.agent.config`; Keycloak realm group paths and token exchange for each audience |
| ADR | ✅ | ADR 0020 |

## 13. Recap

Step 10 turned "OPA decides every tool call" into a complete, tested rulebook and a single
checkpoint. The authorising executor asks the policy engine about every call, counts it against a
per-minute limit, runs it and writes the decision and outcome to the audit log. Tools act on
behalf of the user through token exchange and are refused when there is no user. Cerbos can
replace OPA and is proven to decide the same way; OpenFGA adds "is this person related to this
object?" on top. A call for another tenant's data is denied and audited, end to end.
