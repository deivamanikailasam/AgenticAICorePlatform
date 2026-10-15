#!/usr/bin/env bash
# Encrypt secrets/dev.env -> secrets/dev.enc.env with SOPS + age, then delete the plaintext.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SOPS_AGE_RECIPIENTS:?set SOPS_AGE_RECIPIENTS to the Project 0 age public key (see docs/setup/local.md)}"
[[ -f secrets/dev.env ]] || { echo "secrets/dev.env not found; copy secrets/dev.env.example first" >&2; exit 1; }
mv secrets/dev.env secrets/dev.enc.env
sops --encrypt --in-place secrets/dev.enc.env || { mv secrets/dev.enc.env secrets/dev.env; exit 1; }
echo "wrote secrets/dev.enc.env; plaintext removed"
