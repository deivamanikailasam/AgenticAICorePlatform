# OpenBao login with the pod's service-account token

## What changed
- `agent_tools.openbao.KubernetesAuth`: logs in at `auth/kubernetes` with the pod's projected
  service-account token (default `/var/run/secrets/openbao/token`), read again at every login
  because the kubelet rotates it.
- `default_resolver` uses it when `AGENT_BAO_K8S_ROLE` is set; AppRole stays for hosts outside
  Kubernetes. Staging and prod still refuse to start without OpenBao, and the error now names both
  ways in.
- agent-api switches to per-agent identities when either login is configured.

## Why
Step 38 deploys to Kubernetes. With AppRole, a secret id would have to be stored in the cluster;
with Kubernetes auth the pod proves who it is with a token that lives for minutes, so there is no
static secret to leak (secrets only from OpenBao).

## How to check it
`uv run pytest libs/agent-tools/tests/test_openbao.py -m "not integration"`: login with the
token, a rotated token used at the next login, a missing token refused, prod resolver uses
Kubernetes auth and refuses to start without OpenBao.

## Risks and follow-ups
- OpenBao's Kubernetes auth method and a role per environment must be configured in Project 0's
  OpenBao (VERIFY-ON-MAC); the deployment's role names are in `deploy/`.
