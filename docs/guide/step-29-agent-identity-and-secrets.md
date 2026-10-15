# Step 29 · Agent identity and secrets

## 1. At a glance

| | |
|---|---|
| **Goal** | Every agent acts as itself, for the user, with credentials that live for minutes and that the model never sees |
| **Time** | About two days |
| **You end up with** | OpenBao provisioned for the platform; a Keycloak client per agent that logs in to OpenBao as itself; two-hop delegated token exchange; database credentials issued per tool call, revoked after and audited by lease; secrets and gateway keys from OpenBao; cloud secret managers and SPIFFE as documented stubs |
| **New words** | service identity, delegation, down-scoping, token exchange, dynamic credential, lease, revocation, AppRole, JWT auth, workload identity |

## 2. The big idea

Three questions must always have a precise answer:
- **Who is acting?** The agent, not "the platform".
- **For whom?** The user who asked, with that user's rights and no more.
- **With what key?** A key made for this one call, which stops working minutes later.

And the model, which reads untrusted text all day, must never hold a key at all.

> *Analogy:* a hotel. Each staff member has their own badge (the agent's identity). When a
> guest asks for room service, the front desk writes a ticket for that guest and that room only
> (the delegated token). The porter gets a key card that opens one store room and expires in
> five minutes (the lease), hands it back after, and the log shows guest, porter and card number.
> The guest never holds the master key, and neither does anyone who talks to the guest.

## 3. What we're doing

1. Add OpenBao to the stand-in and provision it: a KV store for the platform's secrets, a
   database engine that creates short-lived Postgres users, and a read-only identity for the
   runtime.
2. Define a credential-broker contract and an OpenBao broker. Issue a credential per tool call,
   revoke it afterwards, and audit only the lease.
3. Give each agent its own Keycloak client, which also logs in to OpenBao as that agent with a
   policy limited to the agent's spec.
4. Delegate user requests in two hops (platform → agent → service), each narrowing the token.
5. Make services read every secret, including the LiteLLM keys, from OpenBao.
6. Record the cloud alternatives and workload identity as stubs with an ADR.

## 4. Why we're doing this

Identity and privilege abuse is OWASP risk ASI03. An agent with a long-lived, broad credential
turns one prompt injection into a data breach. A shared platform identity hides which agent did
what. Credentials in prompts or traces leak to every log reader.

> *Watch out:* short-lived does not mean safe to show. A five-minute password in a trace is
> still a five-minute window for whoever reads the trace. The rule is "never seen", and short
> life is the backstop.

## 5. Key ideas

- **Dynamic credentials.**
  - OpenBao creates a new database user for each call, valid for 5 minutes (15 at most).
  - The user belongs only to a read-only group role.
  - Revoking ends its open sessions and drops it. If nobody revokes it, it expires anyway.
- **Credentials at call time only.**
  - The spec names a credential source per tool, never a secret.
  - The platform issues the credential after policy allows the call, and hands it to the tool
    only for that call.
  - Any result containing it is withheld from the model.
- **Audit the lease, not the secret.** Each tool call's audit record has the user and agent
  (from the run context) and the lease: id, source, who it was issued to, expiry, and whether it
  was revoked.
- **One identity per agent.**
  - `agent-<name>` in Keycloak.
  - Its token logs in to OpenBao as that agent. OpenBao's policy for it covers only the
    credential sources its spec grants.
- **Delegation in two hops.**
  - The platform turns the user's token into one addressed to the agent.
  - The agent turns that into one for the downstream service, and only for services its spec
    needs.
  - The final token says "alice, via agent-invoice-triage, for rag-core".
- **Secrets only from OpenBao.**
  - Services read secrets with a read-only AppRole.
  - Environment variables are a dev and CI fallback only. In staging and prod a service without
    OpenBao won't start.

## 6. How to do it

```yaml
tools:
  - ref: local://platform/sql_read
    credentials: agent-db/creds/tool-reader      # an OpenBao role; never a DSN or a password
    config:
      database: agent
      queries:
        supplier_by_name: "select name, abn, status from tool_data.suppliers where name = %(name)s"
```

```bash
just provision                    # … provision-keycloak (agent clients), provision-openbao (last)
just provision-openbao            # second run: every line says "exists"
# move AGENT_BAO_ROLE_ID / AGENT_BAO_SECRET_ID from .tmp/openbao-approle.env into secrets/dev.env
just with-secrets just up         # agent-api and agent-worker: per-agent identities, broker on
```

What a tool call's audit record holds:

```json
{"type": "tool.call", "ctx": {"user_id": "alice", "agent_id": "supplier-check", "...": "..."},
 "payload": {"tool": "sql_read", "decision": "allow", "ok": true,
   "credential": {"lease_id": "agent-db/creds/tool-reader/8WSQ…", "source": "openbao:agent-db/creds/tool-reader",
                  "issued_to": "supplier-check", "expires_at": "…", "revoked": true}}}
```

## 7. Benefits

- A stolen or leaked credential is useful for minutes at most, and usually for no time at all,
  because it's revoked when the call ends.
- One agent's mistake or compromise can't reach credentials meant for another agent.
- Downstream services see who the user is and which agent is acting.
- No secret files to hand around. LiteLLM keys go straight from provisioning into OpenBao.

## 8. Drawbacks and risks

- Each credentialed call adds OpenBao round trips and a fresh database login.
- Keycloak's issuer name differs between containers and the host here, so OpenBao checks the
  realm's keys, audience and client rather than the issuer. Set `AGENT_BAO_JWT_ISSUER` once
  Keycloak has a fixed hostname.
- Outside Kubernetes, the runtime's AppRole secret id is a bootstrap secret to protect.
- The run API emits traces only from Step 30. The "no secret in traces" check runs on the traced
  runner until then.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| OpenBao | HashiCorp Vault (same API) | Vault Enterprise is already the company standard |
| OpenBao | Cloud IAM and secret managers (stubs) | You run on one cloud: AWS STS or RDS IAM auth, GCP service-account tokens, Azure managed identity |
| AppRole for the runtime | SPIFFE/SPIRE workload identity (stub) | Kubernetes, where each pod can prove what it is without a secret |
| Dynamic credentials | Static secrets in KV, rotated | The target system can't create short-lived users or tokens |
| Two-hop delegation | One platform identity for everything | Never for agents that act for users. Acceptable for a single-purpose internal job |

## 10. Which approach fits which situation

- **Agents that read business databases:** `sql_read` with named queries and a read-only
  credential role.
- **Agents that call services for a user (rag-core, MCP servers, A2A partners):** on-behalf-of
  auth. Delegation through the agent happens automatically.
- **Batch agents with no user:** the agent's own identity only. Grant credentials, not user
  audiences.
- **Cloud deployment:** keep the contracts, and finish the cloud broker for your provider in
  Step 37.

## 11. Common mistakes

- Putting a DSN or API key in `config` "just for now". The spec schema refuses credentials in
  the `credentials` field; review `config` too.
- Giving the runtime identity permission to issue tool credentials. It may only read KV, and
  each agent issues credentials as itself.
- Logging tool results while debugging a credentialed tool. The platform withholds a result
  containing the credential, but your own debug print won't.
- Long default TTLs on database roles. Provisioning refuses anything over 15 minutes.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A tool call's audit record names user, agent and credential lease | ✅ | `test_credentials_in_runs.py` through the run API against OpenBao and Postgres: the `tool.call` record names `alice`, `supplier-check` and the lease (`openbao:agent-db/creds/tool-reader`, `revoked: true`); the database user is dropped |
| A leaked lease expires within minutes | ✅ | Provisioned tool role: 5-minute leases, refused above 15 minutes. A 3-second lease that nobody revokes stops working (`valid` is false and login fails); revocation ends open sessions at once (`test_openbao.py`, live) |
| No secret appears in any trace or prompt | ✅ (run-API spans: Step 30) | The issued user name and password are in no prompt, run event, run record or audit record; a traced run's spans contain neither; a tool that echoes its credential has its result withheld |
| Each agent has a Keycloak service identity | ✅ | `agent-<name>` for all 7 specs (provisioning, idempotent). Live: its token names it and OpenBao accepts it as that agent only |
| Delegated, down-scoped tokens | ✅ | Live: alice's request reaches rag-core as `aud=rag-core`, `azp=agent-identity-probe`, with her groups; an agent without rag-core in its spec is refused; a user token sent straight to an agent is refused |
| Tools get credentials from OpenBao at call time, never from prompt or environment | ✅ | Credentials come only from the broker inside the authorising executor. Staging and prod refuse env secrets; the runtime identity can't issue tool credentials; an agent granted nothing is refused |
| SPIFFE/SPIRE, cloud IAM and secret managers | stub | Registered, refuse with ADR 0040; finished in Step 37 against a real cluster or account |
| ADR | ✅ | ADR 0040 |

## 13. Recap

Agents now have their own identities:
- Requests for a user travel as narrow, delegated tokens that name both the user and the agent.
- Tools get a database login made for one call, which is revoked when the call ends and expires
  within minutes anyway.
- The audit shows who, for whom and with which lease. The model, the traces and the logs never
  see the key.
- Every secret the platform needs now lives in OpenBao.
