"""Governance integrity — verify PRINCIPLES.md has not been tampered with.

The constitution is SHA-256 baselined in governance/integrity.yaml and checked at startup.
A mismatch without a deliberate re-baseline is flagged. This is Tenet 9 (Governance Integrity):
the principles are only "immutable" if tampering is DETECTABLE and change is DELIBERATE.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml

_ROOT = Path(__file__).resolve().parent.parent


class GovernanceIntegrity:
    def __init__(self, project_root: Optional[str] = None):
        self.root = Path(project_root) if project_root else _ROOT
        self.principles_path = self.root / "PRINCIPLES.md"
        self.integrity_path = self.root / "governance" / "integrity.yaml"

    def compute_hash(self, filepath: Path) -> str:
        return hashlib.sha256(filepath.read_bytes()).hexdigest() if filepath.exists() else ""

    def _stored_hash(self) -> Optional[str]:
        if not self.integrity_path.exists():
            return None
        data = yaml.safe_load(self.integrity_path.read_text(encoding="utf-8")) or {}
        return (data.get("principles") or {}).get("sha256_hash")

    def verify_principles_integrity(self) -> dict:
        if not self.principles_path.exists():
            return {"valid": False, "message": "PRINCIPLES.md not found — governance document missing!",
                    "current_hash": None, "stored_hash": None}
        current = self.compute_hash(self.principles_path)
        stored = self._stored_hash()
        if stored is None:
            return {"valid": False,
                    "message": "No integrity baseline — run scripts/rebaseline_integrity.py to establish one.",
                    "current_hash": current, "stored_hash": None}
        if current == stored:
            return {"valid": True, "message": "PRINCIPLES.md integrity verified — no tampering detected.",
                    "current_hash": current, "stored_hash": stored}
        return {"valid": False,
                "message": "PRINCIPLES.md hash MISMATCH — possible tampering, or an un-rebaselined change. "
                           "Review the diff; if legitimate, re-baseline through the governance handshake.",
                "current_hash": current, "stored_hash": stored}
