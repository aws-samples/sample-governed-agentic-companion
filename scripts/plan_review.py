"""Review a saved Terraform plan before a human applies it (D27.3).

Usage:
    terraform show -json <plan> > <plan>.json        # local, read-only
    python3 scripts/plan_review.py [--forbid-cognito-groups] <plan>.json

Prints one line per resource change (address and action; no-op entries are hidden). An update
line lists the changed top-level attribute NAMES only: values are never printed, so no secret in
the plan is echoed. Totals follow.

Exit codes:
    0  nothing protected is deleted or replaced
    1  a protected resource is deleted or replaced, or (with --forbid-cognito-groups) any
       aws_cognito_user_group has an action other than no-op; each offending address is named
    2  the input is not a `terraform show -json` plan document

Protected resources are matched on each entry's `type` (and `name` where stated), never on the
address prefix, so module-nested resources (module.<m>.aws_kms_key.<n>) are caught.

Stdlib only and read-only: it reads one file and writes to stdout/stderr (Tenet 1: it applies
nothing; a human decides).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional, Tuple

# Protected resource types (D27.3). A delete or replace of any of them fails the review.
PROTECTED_TYPES = frozenset({
    "aws_cognito_user_pool",
    "aws_cognito_user_pool_client",
    "aws_cognito_user_pool_domain",
    "aws_cognito_resource_server",
    "aws_cognito_user_group",
    "aws_s3_bucket_public_access_block",
    "aws_kms_key",
    "aws_kms_alias",
    "aws_ecr_repository",
    "aws_bedrockagentcore_memory",
    "aws_vpc",
    "aws_subnet",
    "aws_vpc_endpoint",
    "aws_bedrockagentcore_gateway",
    "aws_bedrockagentcore_gateway_target",
    "aws_bedrockagentcore_agent_runtime",
    "aws_bedrockagentcore_oauth2_credential_provider",
})
# aws_s3_bucket is protected only for the KB buckets (resource name starting "kb").
PROTECTED_BUCKET_NAME_PREFIX = "kb"
COGNITO_GROUP_TYPE = "aws_cognito_user_group"

ACTION_ORDER = ("create", "update", "delete", "replace", "read")


class NotAPlan(Exception):
    """The input is not a `terraform show -json` plan document."""


def load_plan(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NotAPlan(f"cannot read a JSON document ({type(exc).__name__})") from None
    if not isinstance(doc, dict) or "format_version" not in doc:
        raise NotAPlan("not a terraform show -json document (no format_version)")
    if "planned_values" not in doc and "resource_changes" not in doc:
        raise NotAPlan("not a plan: no planned_values or resource_changes (a state document?)")
    changes = doc.get("resource_changes", [])
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        raise NotAPlan("resource_changes is not a list of objects")
    return doc


def action_of(actions) -> Optional[str]:
    """Map a change's actions list to one word; None for no-op (hidden)."""
    acts = list(actions) if isinstance(actions, list) else []
    if acts in (["no-op"], []):
        return None
    if sorted(acts) == ["create", "delete"]:
        return "replace"
    if len(acts) == 1 and acts[0] in ("create", "update", "delete", "read"):
        return acts[0]
    return "+".join(str(a) for a in acts)


def changed_attributes(change: dict) -> List[str]:
    """Top-level attribute names whose value differs between before and after (or becomes
    unknown). Names only; values are never returned."""
    before = change.get("before") if isinstance(change.get("before"), dict) else {}
    after = change.get("after") if isinstance(change.get("after"), dict) else {}
    unknown = change.get("after_unknown") if isinstance(change.get("after_unknown"), dict) else {}
    names = set()
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            names.add(key)
    for key, val in unknown.items():
        if val not in (False, None, {}, []):
            names.add(key)
    return sorted(str(n) for n in names)


def is_protected(entry: dict) -> bool:
    rtype = entry.get("type", "")
    if rtype in PROTECTED_TYPES:
        return True
    return rtype == "aws_s3_bucket" and str(entry.get("name", "")).startswith(PROTECTED_BUCKET_NAME_PREFIX)


def review(doc: dict, forbid_cognito_groups: bool = False) -> Tuple[List[str], List[str], Dict[str, int]]:
    """Return (listing lines, violation lines, totals)."""
    lines: List[str] = []
    violations: List[str] = []
    totals = {a: 0 for a in ACTION_ORDER}
    for entry in doc.get("resource_changes", []):
        change = entry.get("change") if isinstance(entry.get("change"), dict) else {}
        action = action_of(change.get("actions"))
        if action is None:
            continue
        address = str(entry.get("address", "<no address>"))
        totals[action] = totals.get(action, 0) + 1
        line = f"{action:8} {address}"
        if action == "update":
            attrs = changed_attributes(change)
            if attrs:
                line += "  (" + ", ".join(attrs) + ")"
        lines.append(line)
        if entry.get("mode", "managed") != "managed":
            continue
        if is_protected(entry) and action in ("delete", "replace"):
            violations.append(f"PROTECTED {action}: {address}")
        elif forbid_cognito_groups and entry.get("type") == COGNITO_GROUP_TYPE:
            violations.append(f"COGNITO GROUP {action}: {address} (--forbid-cognito-groups)")
    return lines, violations, totals


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Review a terraform show -json plan before apply.")
    parser.add_argument("plan_json", help="the output of terraform show -json <plan>")
    parser.add_argument("--forbid-cognito-groups", action="store_true",
                        help="fail on any aws_cognito_user_group change other than no-op")
    args = parser.parse_args(argv)
    try:
        doc = load_plan(args.plan_json)
    except NotAPlan as exc:
        print(f"plan_review: {exc}", file=sys.stderr)
        return 2
    lines, violations, totals = review(doc, args.forbid_cognito_groups)
    for line in lines:
        print(line)
    print("Totals: " + ", ".join(f"{totals[a]} {a}" for a in ACTION_ORDER)
          + "".join(f", {n} {a}" for a, n in totals.items() if a not in ACTION_ORDER))
    if violations:
        for v in violations:
            print(v)
        print(f"plan_review: FAIL ({len(violations)} protected change(s)); do not apply")
        return 1
    print("plan_review: PASS (nothing protected is deleted or replaced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
