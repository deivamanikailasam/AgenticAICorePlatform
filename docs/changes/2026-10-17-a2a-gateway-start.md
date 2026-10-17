# a2a-gateway resolves its signing key inside its own event loop

## What changed
- `serve()` now awaits the card-signing key from OpenBao (`signing_key()`) and passes it to
  `build()`, instead of `build()` calling `asyncio.run()` inside the loop `serve()` already runs.
- New test: `services/a2a-gateway/tests/test_main.py`.

## Why
In deploy-e2e on 902589c, every pod except a2a-gateway became healthy, so the Step 38 OpenBao fix
worked. The a2a-gateway crash-looped with `RuntimeError: asyncio.run() cannot be called from a
running event loop`, and that left the Argo CD application Degraded.

## How to check it
`uv run pytest services/a2a-gateway/tests/test_main.py`: it builds the gateway inside a running
loop with the key from a resolver. With the old `build` the same test fails with the nested-loop
error.

## Risks and follow-ups
The fix reaches the cluster only with new images: the overlays are re-pinned in a separate
commit.
