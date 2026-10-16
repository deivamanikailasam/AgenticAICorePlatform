# Service images built, scanned, signed and published by digest

## What changed
- New `.github/workflows/images.yml`. For each service image (agent-api, agent-worker,
  mcp-gateway, a2a-gateway, ops) it builds once per commit, pushes to
  `ghcr.io/deivamanikailasam/agent-platform/<image>`, attaches an SBOM and provenance, scans with
  Trivy 0.75.0 (fails on fixable critical vulnerabilities, reports high), signs keylessly with
  cosign v3.1.3 under this workflow's identity and verifies the signature. The digest is recorded
  as an artifact and in the run summary.
- `deploy/third-party-images.txt`: third-party images for the deployment, pinned by digest and
  checked against their registries. `resolve` lines print a digest so a new image can be pinned
  (Argo CD, Dex, Redis and KEDA are listed this way because their registries are blocked from
  the build container).
- Tools are downloaded with their published SHA-256 checked; no third-party actions are added.

## Why
Step 38 deploys by digest only. Overlays need real, signed digests; the release flow in the spec
asks for SBOM, image scan and signing.

## How to check it
The `images` workflow run for this commit: five images with digests in the summary, Trivy and
`cosign verify` passing, and the third-party digests printed.

## Risks and follow-ups
- Packages on GHCR are private by default; the cluster pulls with a registry credential
  (deploy docs). Making them public is the owner's choice.
- Admission-time signature verification in the cluster (Sigstore policy controller or Kyverno)
  is documented as the next hardening step.
