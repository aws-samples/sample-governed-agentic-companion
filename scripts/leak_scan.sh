#!/usr/bin/env bash
# Customer-data / secret leak scan — fails if a customer-identifying token, a real
# account id, or secret-shaped material appears in committed content. Used by CI
# (.gitlab-ci.yml) and runnable by hand:
#   ./scripts/leak_scan.sh
#
# The knowledge base, docs, and code MUST stay customer-agnostic and secret-free
# (PRINCIPLES.md Tenet 6, AGENTS.md). This is a guardrail, not a guarantee — keep it
# conservative and add patterns whenever a new class of identifier is discovered.
#
# This is a STARTER KIT, so the default patterns are GENERIC shapes (real AWS account
# ids, access keys, PEM/JWT material) plus a defensive list of identifiers from the
# solution this kit was generalized from — so a fork that copies content from an
# upstream engagement can't silently reintroduce a customer name. A fork SHOULD add
# its own engagement's customer tokens to CUSTOMER_PATTERNS below.
#
# Exit 0 = clean, Exit 1 = potential leak found (fails the pipeline).
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR" || exit 1

# --- Secret / credential SHAPES (never legitimate in a customer-agnostic repo) -------
# AWS access key ids, PEM private-key headers, and a 12-digit AWS account id appearing
# next to the word "account" (a bare 12-digit number is too noisy to flag globally).
SECRET_PATTERNS='(AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|aws_secret_access_key\s*=\s*[A-Za-z0-9/+]{20,}'

# --- Customer / engagement identifiers -----------------------------------------------
# A fork ADDS its own engagement's customer/project code names here (pipe-separated, matched
# case-insensitively) so a copy-paste of customer-identifying content is caught before commit.
# Empty by default — this starter kit ships no customer identifiers.
#   Example: CUSTOMER_PATTERNS='acmecorp|project-zephyr|internal-alias'
CUSTOMER_PATTERNS=''

PATTERNS="${SECRET_PATTERNS}${CUSTOMER_PATTERNS:+|${CUSTOMER_PATTERNS}}"

# Scan tracked content only. Exclude this script (it names the patterns by necessity),
# the .git dir, and the local virtualenv.
MATCHES="$(git grep -inE "$PATTERNS" -- \
  ':!scripts/leak_scan.sh' \
  ':!.venv' \
  2>/dev/null || true)"

if [ -n "$MATCHES" ]; then
  echo "LEAK SCAN: FAIL — potential customer-identifying or secret-shaped tokens found:"
  echo "$MATCHES"
  echo ""
  echo "Remove/generalize the above before committing. See AGENTS.md (customer-agnostic rule)"
  echo "and add any new customer identifiers to CUSTOMER_PATTERNS in this script."
  exit 1
fi

echo "LEAK SCAN: CLEAN — no known customer-identifying or secret-shaped tokens found."
exit 0
