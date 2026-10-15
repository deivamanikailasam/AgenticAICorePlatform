"""Create the artefact bucket in Project 0's SeaweedFS S3. Idempotent."""

from __future__ import annotations

import os
import sys

from agent_tools.artefacts import S3ArtefactStore


def main() -> int:
    endpoint = os.environ.get("P0_S3_ENDPOINT", "http://127.0.0.1:8333")
    bucket = os.environ.get("AGENT_ARTEFACT_BUCKET", "agent-artefacts")
    access, secret = os.environ.get("P0_S3_ACCESS_KEY"), os.environ.get("P0_S3_SECRET_KEY")
    if not access or not secret:
        print("set P0_S3_ACCESS_KEY and P0_S3_SECRET_KEY (just with-secrets ...)")
        return 2
    print(
        f"artefacts: bucket {bucket}: {S3ArtefactStore(endpoint, bucket, access, secret).ensure_bucket()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
