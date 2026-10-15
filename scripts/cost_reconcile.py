"""Compare the platform's cost accounting with the gateway's, per role key (Step 26).

Platform side: ``agent_runtime.usage_ledger`` (every model call's cost in USD as LiteLLM reported
it), grouped by the key that paid (``key_name``: a role's own key, or the fallback it used). Gateway side: LiteLLM's spend logs for each ``agent-platform-<role>`` key over the same
UTC dates. A role matches when the two agree within ``--tolerance`` (relative, default 0.5%) or
USD 0.000001. LiteLLM writes spend in batches, so ``--wait`` polls until they match or time out.

    just cost-reconcile                      # today (UTC)
    just cost-reconcile --start 2026-10-01 --end 2026-10-15 --wait 90
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg

ALIAS_PREFIX = "agent-platform-"


def platform_spend(dsn: str, start: date, end: date) -> dict[str, float]:
    since = datetime.combine(start, datetime.min.time(), UTC)
    until = datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "select key_name, coalesce(sum(cost_usd), 0) from agent_runtime.usage_ledger"
            " where at >= %s and at < %s group by key_name",
            (since, until),
        ).fetchall()
    return {str(r[0]): float(r[1]) for r in rows}


def gateway_spend(
    client: httpx.Client, base: str, master_key: str, start: date, end: date
) -> dict[str, float]:
    auth = {"Authorization": f"Bearer {master_key}"}
    resp = client.get(
        f"{base}/key/list", params={"return_full_object": "true", "size": 100}, headers=auth
    )
    resp.raise_for_status()
    tokens = {
        str(k["key_alias"])[len(ALIAS_PREFIX) :]: str(k["token"])
        for k in resp.json().get("keys", [])
        if isinstance(k, dict) and str(k.get("key_alias") or "").startswith(ALIAS_PREFIX)
    }
    out: dict[str, float] = {}
    for role, token in tokens.items():
        # LiteLLM filters before midnight of end_date (exclusive), and returns one row per day
        logs = client.get(
            f"{base}/spend/logs",
            params={
                "api_key": token,
                "start_date": start.isoformat(),
                "end_date": (end + timedelta(days=1)).isoformat(),
            },
            headers=auth,
        )
        logs.raise_for_status()
        out[role] = sum(
            float(day.get("spend") or 0)
            for day in logs.json()
            if start.isoformat() <= str(day.get("startTime", ""))[:10] <= end.isoformat()
        )
    return out


@dataclass(frozen=True)
class Row:
    role: str
    platform_usd: float
    gateway_usd: float

    @property
    def diff(self) -> float:
        return self.platform_usd - self.gateway_usd

    def ok(self, tolerance: float) -> bool:
        return abs(self.diff) <= max(1e-6, tolerance * max(self.platform_usd, self.gateway_usd))


def compare(platform: Mapping[str, float], gateway: Mapping[str, float]) -> list[Row]:
    roles = sorted(set(platform) | set(gateway))
    return [Row(r, platform.get(r, 0.0), gateway.get(r, 0.0)) for r in roles]


def render(rows: Sequence[Row], tolerance: float, rate: float, start: date, end: date) -> str:
    lines = [
        f"Cost reconciliation · {start} to {end} (UTC) · USD as LiteLLM reports it; AUD at {rate}",
        "",
        "| Role | Platform (USD) | Gateway (USD) | Difference | Match |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.role} | {r.platform_usd:.6f} | {r.gateway_usd:.6f} | {r.diff:+.6f} | "
            f"{'yes' if r.ok(tolerance) else 'NO'} |"
        )
    total_p = sum(r.platform_usd for r in rows)
    total_g = sum(r.gateway_usd for r in rows)
    lines.append(f"| total | {total_p:.6f} | {total_g:.6f} | {total_p - total_g:+.6f} | |")
    lines.append(f"\nTotal in AUD: platform {total_p * rate:.4f}, gateway {total_g * rate:.4f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from agent_spec.platform import load_platform

    p = argparse.ArgumentParser(description="Compare platform and gateway cost accounting")
    today = datetime.now(UTC).date()
    p.add_argument("--start", type=date.fromisoformat, default=today)
    p.add_argument("--end", type=date.fromisoformat, default=today)
    p.add_argument("--tolerance", type=float, default=0.005)
    p.add_argument("--wait", type=float, default=0.0, help="seconds to poll for LiteLLM's writes")
    args = p.parse_args(argv)
    dsn, master = os.environ.get("AGENT_DB_DSN"), os.environ.get("LITELLM_MASTER_KEY")
    if not dsn or not master:
        print("set AGENT_DB_DSN and LITELLM_MASTER_KEY (just with-secrets just cost-reconcile)")
        return 2
    base = os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000")
    deadline = time.monotonic() + args.wait
    with httpx.Client(timeout=20.0) as client:
        while True:
            rows = compare(
                platform_spend(dsn, args.start, args.end),
                gateway_spend(client, base, master, args.start, args.end),
            )
            matched = all(r.ok(args.tolerance) for r in rows)
            if matched or time.monotonic() >= deadline:
                break
            time.sleep(5)
    print(render(rows, args.tolerance, load_platform().currency.usd_to_aud, args.start, args.end))
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
