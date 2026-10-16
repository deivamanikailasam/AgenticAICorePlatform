# Stand-in: LiteLLM uses its bundled model cost map

**Step:** 33 · **Commit:** fix(stack): LiteLLM uses its bundled cost map · **Date:** 2026-10-16

## What changed
The stand-in LiteLLM sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, so it reads the cost map bundled in the image instead of downloading it from GitHub at start.

## Why
In this build container the download fails TLS verification, because the proxy re-signs traffic. LiteLLM v1.105.1 then logs a warning about the failure, and that warning crashes it with `ImportError: cannot import name 'LoggerContext' from partially initialized module 'litellm.rust_bridge.catalog'`. The gateway exited on start.

Using the bundled map removes a network dependency from the stand-in, which Step 33 wants anyway: the same image always gives the same prices. The stand-in's own models set their prices explicitly in `litellm.yaml`.

## How to check it
`docker compose -f tests/stack/compose.p0-standin.yaml up -d --wait litellm` reports healthy. `libs/agent-runtime/tests/test_gateway_integration.py` passes, including its cost checks.

## Risks and follow-ups
Prices for models not listed in `litellm.yaml` come from the image's bundled map, as of the image's release. The stand-in only uses its own mock models.
