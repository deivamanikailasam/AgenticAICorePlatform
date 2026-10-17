"""Generated deployment config (Step 38).

deploy_config write   # the agent specs each environment mounts, from agents/ and deploy/releases
deploy_config check   # the generated files are current

* **dev** (``deploy/components/head-specs``): every spec in ``agents/`` at main's head.
* **staging, prod** (``deploy/overlays/<env>/released``): the snapshots the environment's
  releases file names (``deploy/specs/<agent>/<version>/``).

The rendering is ``agent_governance.release_repo.ReleaseRepo``, shared with agent projects
(``agentctl``, Step 39).
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_governance.release_repo import ReleaseRepo

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    repo = ReleaseRepo(ROOT)
    if argv[:1] == ["write"]:
        for path in repo.write_config():
            print(f"deploy-config: wrote {path.relative_to(ROOT)}")
        return 0
    total, stale = repo.stale_config()
    for path in stale:
        print(
            f"deploy-config: {path.relative_to(ROOT)} is stale: run just deploy-config",
            file=sys.stderr,
        )
    print(f"deploy-config: {total} generated files, {'stale' if stale else 'up to date'}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
