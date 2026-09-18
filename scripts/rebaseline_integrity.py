#!/usr/bin/env python3
"""Re-baseline the PRINCIPLES.md SHA-256 in governance/integrity.yaml.

Run this ONLY after a DELIBERATE, reviewed change to PRINCIPLES.md (the governance handshake):
state intent + justification, review the diff, then re-baseline. This is a human-run step.

Usage:  python scripts/rebaseline_integrity.py --justification "why the constitution changed"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--justification", required=True, help="why PRINCIPLES.md changed")
    ap.add_argument("--author", default="<governance-admin>")
    args = ap.parse_args()

    principles = _ROOT / "PRINCIPLES.md"
    integrity = _ROOT / "governance" / "integrity.yaml"
    new_hash = hashlib.sha256(principles.read_bytes()).hexdigest()

    data = yaml.safe_load(integrity.read_text(encoding="utf-8")) if integrity.exists() else {}
    data = data or {}
    p = data.setdefault("principles", {})
    old_hash = p.get("sha256_hash")
    p["file"] = "PRINCIPLES.md"
    p["sha256_hash"] = new_hash
    p["last_verified"] = _dt.date.today().isoformat()
    p.setdefault("change_history", []).insert(0, {
        "date": _dt.date.today().isoformat(),
        "author": args.author,
        "justification": args.justification,
        "previous_sha256": old_hash,
        "new_sha256": new_hash,
    })
    integrity.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
                         encoding="utf-8")
    print(f"Re-baselined PRINCIPLES.md: {old_hash} -> {new_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
