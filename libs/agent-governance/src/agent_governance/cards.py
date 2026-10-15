"""Agent cards and system cards (Step 32), generated from the specs, the tool registry's cards and
``platform.yaml``.

Every field is derived from configuration that is enforced at run time (budgets, tools and their
side effects, approvals, guardrails, memory). Nothing is typed in by hand, so a card can't promise
more than the platform enforces. Eval results are cited only from a recorded run
(``evals/results/<agent>.json``); with none recorded, the card says so.
"""

from __future__ import annotations

from typing import Any

from agent_spec import AgentSpec

from agent_governance.inventory import Entry, Inventory

#: always on, whatever the spec lists (Step 27)
BASELINE_GUARDRAILS = ("prompt_injection", "secret_leak")


def _scheme(ref: str) -> str:
    return ref.split("://", 1)[0]


def tool_rows(spec: AgentSpec, inv: Inventory) -> list[dict[str, Any]]:
    """Each tool the agent may call: what it is, its side effect and data, and its approval."""
    servers = {s["name"]: s for s in (inv.platform.get("mcp") or {}).get("servers", [])}
    rows = []
    for t in spec.tools:
        card = inv.tool_cards.get(t.ref)
        row: dict[str, Any] = {"name": t.name, "ref": t.ref, "kind": _scheme(t.ref)}
        if card is not None:
            row |= {
                "version": card["version"],
                "side_effect": card["side_effect"],
                "data_classification": card["data_classification"],
            }
        elif row["kind"] == "mcp":
            server, _, tool = t.ref.removeprefix("mcp://").partition("/")
            row["data_classification"] = "per server"
            if server not in servers:
                row |= {"version": "-", "side_effect": "undeclared", "unlisted_server": server}
            else:
                declared = (servers[server].get("side_effects") or {}).get(tool)
                # MCP annotations from untrusted servers are ignored (Step 7): `write` unless the
                # platform owner declared otherwise
                row |= {"version": "per server", "side_effect": declared or "write (MCP default)"}
        else:
            row |= {"version": "-", "side_effect": "-", "data_classification": "-"}
        if t.approval and t.approval.required:
            who = t.approval.approvers + (" (two people)" if t.approval.two_person else "")
            row["approval"] = who
        else:
            row["approval"] = "none"
        if t.credentials:
            row["credentials"] = f"short-lived, per call ({t.credentials})"
        rows.append(row)
    return rows


def models(spec: AgentSpec, inv: Inventory) -> dict[str, str]:
    """The alias each role resolves to: the spec's own choice, else the platform's default."""
    roles = dict(((inv.platform.get("models") or {}).get("roles")) or {})
    roles.pop("embedder", None)
    return dict(sorted({**roles, **spec.models}.items()))


def guardrails(spec: AgentSpec) -> list[str]:
    names = [g.name for g in spec.guardrails]
    return [*BASELINE_GUARDRAILS, *[n for n in names if n not in BASELINE_GUARDRAILS]]


def data(spec: AgentSpec, tools: list[dict[str, Any]]) -> list[str]:
    out = []
    classes = sorted({t["data_classification"] for t in tools if t["data_classification"] != "-"})
    if classes:
        out.append(f"Tool data: {', '.join(classes)}")
    lt = spec.memory.long_term
    if lt is not None:
        ttl = f", kept {lt.ttl_days} days" if lt.ttl_days else ""
        out.append(f"Long-term memory: {lt.store}, per {lt.scope}, policy {lt.policy}{ttl}")
    if spec.memory.short_term == "checkpoint":
        out.append("Run state: checkpoints (encrypted at rest when durable)")
    out.append("Prompts and responses: Langfuse only, redacted (Step 30)")
    return out


def limitations(spec: AgentSpec, tools: list[dict[str, Any]]) -> list[str]:
    b = spec.budgets
    stops = [f"{b.max_steps} steps", f"{b.max_wall_seconds:g} s"]
    if b.max_tokens:
        stops.append(f"{b.max_tokens} tokens")
    if b.max_cost_aud:
        stops.append(f"AUD {b.max_cost_aud:.2f}")
    if b.max_tool_calls:
        stops.append(f"{b.max_tool_calls} tool calls")
    out = [f"Stops at {', '.join(stops)}; a stopped run returns partial output and the reason"]
    if spec.autonomy.value != "workflow":
        out.append(
            "Model-decided steps: outputs can be wrong or incomplete; check them before acting "
            "on anything consequential"
        )
    out.append("Tool results and retrieved text are treated as data, never as instructions")
    unapproved = [
        t["name"]
        for t in tools
        if t["approval"] == "none" and str(t["side_effect"]).startswith(("write", "irreversible"))
    ]
    if unapproved:
        out.append(f"Writes without a person's approval: {', '.join(unapproved)}")
    unlisted = sorted({t["unlisted_server"] for t in tools if "unlisted_server" in t})
    if unlisted:
        out.append(
            f"MCP server(s) not in platform.yaml's allowlist: {', '.join(unlisted)}. Their tools' "
            "side effects are undeclared here, and compiling against platform.yaml refuses the "
            "spec until the platform owner lists them"
        )
    if spec.agents:
        out.append(
            f"Delegates to {len(spec.agents)} worker(s), at most {b.max_delegation_depth} levels "
            "deep; each worker has its own tools and budget"
        )
    return out


def eval_results(spec: AgentSpec, inv: Inventory) -> str:
    run = inv.eval_results.get(spec.name)
    if run is None:
        suite = spec.evals.suite if spec.evals else "none declared"
        return f"No recorded eval run (suite: {suite})"
    return (
        f"{run['passed']}/{run['total']} passed on {run['dataset']} "
        f"({run['total']} cases, model {run.get('model', '?')}, {run['date']})"
    )


def agent_card(entry: Entry, inv: Inventory) -> dict[str, Any]:
    spec = entry.spec
    tools = tool_rows(spec, inv)
    return {
        "kind": "agent",
        "name": spec.name,
        "version": spec.version,
        "spec": str(entry.path),
        "spec_hash": entry.digest,
        "system": entry.system,
        "worker": entry.worker,
        "purpose": spec.description or "(no description in the spec)",
        "owner": spec.owner,
        "risk_tier": spec.risk_tier.value,
        "autonomy": spec.autonomy.value,
        "pattern": spec.pattern.value,
        "runtime": f"{spec.runtime.adapter}, durability {spec.runtime.durability.value}",
        "models": models(spec, inv),
        "tools": tools,
        "workers": [a.name for a in spec.agents],
        "data": data(spec, tools),
        "guardrails": guardrails(spec),
        "human_oversight": [
            f"{t['name']}: {t['approval']}" for t in tools if t["approval"] != "none"
        ]
        or ["No tool needs approval"],
        "limitations": limitations(spec, tools),
        "eval_results": eval_results(spec, inv),
    }


def system_card(entry: Entry, inv: Inventory) -> dict[str, Any]:
    """A reference app as a whole: the top-level agent, its workers, every tool and service it
    reaches, where people are in the loop, and what the build could not verify."""
    members = [entry, *inv.workers_of(entry.system)]
    cards = [agent_card(m, inv) for m in members]
    tools: dict[str, dict[str, Any]] = {}
    for c in cards:
        for t in c["tools"]:
            tools.setdefault(t["ref"], {**t, "used_by": []})["used_by"].append(c["name"])
    tiers = [c["risk_tier"] for c in cards]
    order = {"low": 0, "medium": 1, "high": 2}
    return {
        "kind": "system",
        "name": entry.system,
        "version": entry.spec.version,
        "purpose": entry.spec.description or "(no description in the spec)",
        "owner": entry.spec.owner,
        "risk_tier": max(
            tiers, key=lambda t: order[t]
        ),  # a system is as risky as its riskiest part
        "agents": [
            {"name": c["name"], "risk_tier": c["risk_tier"], "autonomy": c["autonomy"]}
            for c in cards
        ],
        "tools": sorted(tools.values(), key=lambda t: t["ref"]),
        "services": sorted(
            {"agent-api (runs, approvals, streaming)", "OPA (policy on every tool call)"}
            | ({"MCP servers"} if any(t["kind"] == "mcp" for t in tools.values()) else set())
            | (
                {"OpenBao (per-call credentials)"}
                if any("credentials" in t for t in tools.values())
                else set()
            )
            | ({"memory store"} if any(m.spec.memory.long_term for m in members) else set())
        ),
        "human_oversight": sorted({h for c in cards for h in c["human_oversight"] if ":" in h})
        or ["No tool needs approval"],
        "limitations": list(
            dict.fromkeys(
                f"{c['name']}: {lim}" if lim.startswith("Stops at") else lim
                for c in cards
                for lim in c["limitations"]
            )
        ),
        "eval_results": {c["name"]: c["eval_results"] for c in cards},
    }


def _table(rows: list[tuple[str, str]]) -> list[str]:
    return ["| Field | Value |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows]]


def _tools_table(tools: list[dict[str, Any]], used_by: bool = False) -> list[str]:
    if not tools:
        return ["No tools."]
    head = "| Tool | Ref | Side effect | Data | Approval |" + (" Used by |" if used_by else "")
    sep = "|---|---|---|---|---|" + ("---|" if used_by else "")
    lines = [head, sep]
    for t in tools:
        line = (
            f"| {t['name']} | `{t['ref']}` | {t['side_effect']} | {t['data_classification']} "
            f"| {t['approval']} |"
        )
        if used_by:
            line += f" {', '.join(t['used_by'])} |"
        lines.append(line)
    return lines


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items]


GENERATED = "Generated by `just cards` from the specs and registries — do not edit by hand."


def render_agent(c: dict[str, Any]) -> str:
    rows = [
        ("Version", c["version"]),
        ("Spec", f"`{c['spec']}`"),
        ("Owner", c["owner"]),
        ("Risk tier", c["risk_tier"]),
        ("Autonomy", c["autonomy"]),
        ("Pattern", c["pattern"]),
        ("Runtime", c["runtime"]),
        ("Part of", c["system"] + (" (worker)" if c["worker"] else "")),
        ("Models", ", ".join(f"{r}: `{m}`" for r, m in c["models"].items())),
        ("Guardrails", ", ".join(c["guardrails"])),
        ("Eval results", c["eval_results"]),
    ]
    if c["workers"]:
        rows.insert(8, ("Workers", ", ".join(c["workers"])))
    return "\n".join(
        [
            f"# Agent card: `{c['name']}`",
            "",
            GENERATED,
            "",
            c["purpose"],
            "",
            *_table(rows),
            "",
            "## Tools",
            "",
            *_tools_table(c["tools"]),
            "",
            "## Data",
            "",
            *_bullets(c["data"]),
            "",
            "## Human oversight",
            "",
            *_bullets(c["human_oversight"]),
            "",
            "## Limitations",
            "",
            *_bullets(c["limitations"]),
            "",
        ]
    )


def render_system(c: dict[str, Any]) -> str:
    agents = ["| Agent | Risk tier | Autonomy |", "|---|---|---|"] + [
        f"| [{a['name']}](../agents/{a['name']}.md) | {a['risk_tier']} | {a['autonomy']} |"
        for a in c["agents"]
    ]
    evals = [f"- {name}: {result}" for name, result in c["eval_results"].items()]
    return "\n".join(
        [
            f"# System card: `{c['name']}`",
            "",
            GENERATED,
            "",
            c["purpose"],
            "",
            *_table(
                [
                    ("Version", c["version"]),
                    ("Owner", c["owner"]),
                    ("Risk tier", f"{c['risk_tier']} (the highest of its agents)"),
                    ("Services", ", ".join(c["services"])),
                ]
            ),
            "",
            "## Agents",
            "",
            *agents,
            "",
            "## Tools",
            "",
            *_tools_table(c["tools"], used_by=True),
            "",
            "## Human oversight",
            "",
            *_bullets(c["human_oversight"]),
            "",
            "## Limitations",
            "",
            *_bullets(c["limitations"]),
            "",
            "## Eval results",
            "",
            *evals,
            "",
            f"Lineage: [lineage](../lineage.md#{c['name']}).",
            "",
        ]
    )
