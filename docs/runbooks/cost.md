# AgentTenantCostHigh

**Severity:** ticket · **Fires when:** one tenant's spend is above AUD 50 in the last hour for 5
minutes.

## What it means
A tenant is spending faster than the hourly guard limit: heavy legitimate use, a looping agent,
or abuse. The LiteLLM team budget remains the hard ceiling.

## Check
1. Grafana → *Cost per tenant* and *Cost per run*.
2. Budget trips for the same agents ([budget-trip-spike.md](budget-trip-spike.md)).
3. `just cost-reconcile --start <day> --end <day>`: the platform's ledger against the gateway's
   spend for each key.

## Fix
Lower the agent's `max_cost_aud`, rate-limit the tenant, or stop the agent version. Raise the
guard limit only through a reviewed change if the use is legitimate.

## Escalate
The tenant's account owner and the platform team's cost owner.
