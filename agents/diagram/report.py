"""`<name>.report.json` (schema gac.diagram-report/1): the full quality findings, flagged items,
traceability, sources and the per-element citations (`elements`: each drawn group, node and edge
by its generated id, with the KB fact it came from and the repo file that fact was checked
against), bounded at 256 KiB (design D12, C27). Every string is a validated KB
reference, a generated id, a fixed code or message, a label already in the gated text, or a
policy-checked source string."""

from __future__ import annotations

import json
from typing import List, Optional

from .limits import MAX_ARTIFACT_BYTES
from .style import CAPTION

SCHEMA = "gac.diagram-report/1"


def build_report(*, topic_id: str, title: str, score: float, passed: bool, threshold: float,
                 findings: List[dict], rendered: int, cited: int, flagged: List[dict],
                 generic_icons: List[str], sources: List[str],
                 elements: Optional[List[dict]] = None, plan: Optional[dict] = None) -> str:
    """`plan` (design D5, item 9) is the planner record: planner, scope, selected qids,
    added_by, excluded, unmatched terms, catalogue_sha256, the per-fact status and the views.
    Ids, fixed codes and sanitized terms only."""
    doc = {
        "schema": SCHEMA, "topic": topic_id, "title": title, "caption": CAPTION,
        "quality": {"score": score, "pass": passed, "threshold": threshold, "findings": findings},
        "traceability": {"rendered": rendered, "cited": cited, "flagged": len(flagged)},
        "flagged": flagged[:500], "generic_icons": generic_icons, "sources": sources,
        "elements": list(elements or []),
    }
    if plan is not None:
        doc["plan"] = plan
    text = json.dumps(doc, sort_keys=True, indent=1) + "\n"
    cap = MAX_ARTIFACT_BYTES["report"]
    if len(text.encode("utf-8")) > cap and "plan" in doc and "status" in doc["plan"]:
        doc["plan"] = {k: v for k, v in doc["plan"].items() if k != "status"}
        doc["truncated"] = True
        text = json.dumps(doc, sort_keys=True, indent=1) + "\n"
    while len(text.encode("utf-8")) > cap and (doc["flagged"] or doc["quality"]["findings"]
                                                or doc["elements"]):
        doc["truncated"] = True
        if doc["quality"]["findings"]:
            doc["quality"]["findings"] = doc["quality"]["findings"][:-10]
        elif doc["flagged"]:
            doc["flagged"] = doc["flagged"][:-10]
        else:
            doc["elements"] = doc["elements"][:-10]
        text = json.dumps(doc, sort_keys=True, indent=1) + "\n"
    return text
