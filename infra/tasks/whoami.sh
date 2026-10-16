#!/usr/bin/env bash
# Shows which identity the run has. Read-only. Proves the GitHub OIDC path.
set -euo pipefail
aws sts get-caller-identity
