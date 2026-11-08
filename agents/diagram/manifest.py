"""The pinned, quoted manifest text the gate sees (design D11, D13.3).

Every variable string is a policy-checked label (always double-quoted and followed only by `"`
and then a space, `)`, `,` or the end of the line), a validated KB reference, a generated id, a
fixed code, a number or a hex digest. One label per line. Bounded by construction: at most 10
flagged and 10 quality lines, then `... and N more`; hard cap 10 KiB with tail-only truncation of
the Quality then Flagged sections. After assembly the text is checked with the gate's content
detectors; a BLOCK (impossible by construction) fails closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .limits import ENGINE_TEXT_CAP, MAX_FLAGGED_LINES, MAX_QUALITY_LINES
from .style import CAPTION

logger = logging.getLogger(__name__)

TRUNCATED_LINE = "(output truncated at the limit; full lists in the report)"
FAIL_CLOSED = ("[diagram] I could not assemble this diagram (internal error), so no file was "
               "produced.")
DELIVER_HEAD = "Artefacts in this response (not yet saved):"
NO_DELIVER_HEAD = "Artefacts produced but not returned by this front door:"
NO_DELIVER_HINT = ("To receive the files, call draw_diagram through the local bridge "
                   "(frontdoor/mcp_bridge.py) or run ./run.sh diagram. See docs/FRONT-DOORS.md.")
LOCAL_PREVIEW = ("Local preview without PNG: resvg-py is not installed here (it needs Python 3.10 "
                 "or later). The deployed image produces all three files.")
TEST_FIXTURE = "Rendered with test-fixture icons, not the official AWS Architecture Icons."
TOO_LARGE = ("The diagram is too large for this front door; use the CLI (./run.sh diagram) or "
             "split the topic.")


def edge_suffix(optional: bool, style: str) -> str:
    if style == "denied":
        return " (red dashed)"
    return " (dashed)" if optional else ""


def line_for(element) -> str:
    """The exact manifest line that carries this element's label."""
    kind = element.kind
    if kind == "edge":
        return f'- edge "{element.label}"{edge_suffix(element.optional, element.style)}'
    if kind == "node" and getattr(element, "optional", False):
        return f'- node "{element.label}" (dashed outline)'
    return f'- {kind} "{element.label}"'


@dataclass
class ManifestInput:
    title: str
    topic_id: str
    score: float
    passed: bool
    threshold: float
    rendered: int
    cited: int
    flagged_total: int
    label_lines: List[str]
    unlabelled_edges: int
    flagged_lines: List[str]
    finding_lines: List[str]
    report_file: str
    generic_icons: List[Tuple[str, str]] = field(default_factory=list)   # (label, code)
    artefacts: List[Tuple[str, int, str]] = field(default_factory=list)  # (file, bytes, sha12)
    omitted: List[Tuple[str, str]] = field(default_factory=list)         # (file, reason)
    deliver: bool = True
    draft: bool = False
    hard_lines: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    too_large: bool = False
    #: "Not in the knowledge base: ..." and "Drawn from the knowledge base, ..." (D7.1)
    kb_lines: List[str] = field(default_factory=list)


def finding_line(severity: str, code: str, element: str, message: str) -> str:
    at = f" at {element}" if element else ""
    return f"- {severity.upper()} {code}{at} ({message})"


def _section(head: str, lines: Sequence[str], cap: int, more_hint: str) -> List[str]:
    if not lines:
        return []
    shown = list(lines[:cap])
    out = [head.format(shown=len(shown), total=len(lines))] + shown
    if len(lines) > cap:
        out.append(f"- ... and {len(lines) - cap} more{more_hint}")
    return out


def _fail_closed_check(text: str) -> Tuple[str, bool]:
    from .labels import _gate
    g = _gate()
    blocks = [f.code for f in g.content_findings(text) + g.content_findings_t11(text)
              if f.severity.value == "block"]
    if blocks:
        logger.error("diagram: manifest assembly tripped %s; failing closed", blocks)
        return FAIL_CLOSED, False
    return text, True


def build_text(m: ManifestInput) -> Tuple[str, bool]:
    """Return (text, ok). ok is False when the fail-closed text is returned."""
    head: List[str] = []
    if m.draft:
        head.append(f"DRAFT: quality {m.score:.1f}/100, below the {m.threshold:.0f} pass mark")
        head.extend(m.hard_lines[:MAX_QUALITY_LINES])
    head.append(f'[diagram] "{m.title}" (topic {m.topic_id})')
    head.append(CAPTION)
    verdict = "pass" if m.passed else "below the pass mark"
    head.append(f"Quality {m.score:.1f} of 100, {verdict} (threshold {m.threshold:.0f}). "
                f"Traceability {m.cited} of {m.rendered} rendered elements cite a source; "
                f"{m.flagged_total} facts flagged.")
    head.extend(line for line in m.kb_lines if line)
    labels = ["Rendered labels:"] + m.label_lines
    if m.unlabelled_edges:
        labels.append(f"- {m.unlabelled_edges} unlabelled edge{'s' if m.unlabelled_edges != 1 else ''}")
    flagged = _section("Flagged ({shown} of {total} shown; full list in " + m.report_file + "):",
                       m.flagged_lines, MAX_FLAGGED_LINES, " (run ./run.sh diagram --check-facts)")
    quality = _section("Quality findings ({shown} of {total} shown; full list in the report):",
                       m.finding_lines, MAX_QUALITY_LINES, "")
    generic: List[str] = []
    if m.generic_icons:
        shown = ", ".join(f'"{lab}"' for lab, _ in m.generic_icons[:4])
        more = f", and {len(m.generic_icons) - 4} more" if len(m.generic_icons) > 4 else ""
        codes = "/".join(sorted({c for _, c in m.generic_icons}))
        generic.append(f"Generic icon used for {shown}{more} (WARN {codes})")
    arts: List[str] = []
    if m.too_large:
        arts.append(TOO_LARGE)
    if m.artefacts:
        arts.append(DELIVER_HEAD if m.deliver else NO_DELIVER_HEAD)
        arts += [f"- {f} ({b} B, sha256 {h})" for f, b, h in m.artefacts]
        if not m.deliver:
            arts.append(NO_DELIVER_HINT)
    arts += [f"Not delivered: {f} ({r})" for f, r in m.omitted]
    notes = list(m.notes)

    def assemble(q: List[str], fl: List[str], trunc: bool) -> str:
        parts = head + labels + fl + q + generic + notes + arts
        if trunc:
            parts.append(TRUNCATED_LINE)
        return "\n".join(parts)

    text = assemble(quality, flagged, False)
    trunc = False
    while len(text.encode("utf-8")) > ENGINE_TEXT_CAP and (quality or flagged):
        trunc = True
        if quality:
            quality = quality[:-1]
        else:
            flagged = flagged[:-1]
        text = assemble(quality, flagged, True)
    if trunc:
        logger.info("diagram: engine text truncated at %d bytes", ENGINE_TEXT_CAP)
    return _fail_closed_check(text)


# -- several views (design D7.3) --------------------------------------------------------------

@dataclass
class ViewSection:
    title: str
    view_id: str
    score: float
    passed: bool
    threshold: float
    nodes: int
    edges: int
    groups: int
    context: int
    rendered: int
    cited: int
    draft: bool = False
    hard_lines: List[str] = field(default_factory=list)
    artefacts: List[Tuple[str, int, str]] = field(default_factory=list)
    omitted: List[Tuple[str, str]] = field(default_factory=list)
    #: "" when delivered; else why the view has no artefacts (render_deadline, views_over_limit)
    missing: str = ""


@dataclass
class MultiInput:
    title: str
    sections: List[ViewSection]
    flagged_total: int
    flagged_lines: List[str]
    report_hint: str
    kb_lines: List[str] = field(default_factory=list)
    generic_icons: List[Tuple[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    deliver: bool = True
    too_large: bool = False


def build_multi_text(m: MultiInput) -> Tuple[str, bool]:
    """The text of a multi-view render: per-view titles, quality lines, counts and files (the
    per-element label lines stay in each view's report.json), within ENGINE_TEXT_CAP."""
    n = len(m.sections)
    rendered = sum(s.rendered for s in m.sections if not s.missing)
    cited = sum(s.cited for s in m.sections if not s.missing)
    head = [f'[diagram] "{m.title}" ({n} views)', CAPTION]
    head.append(f"Traceability {cited} of {rendered} rendered elements cite a source across "
                f"the views; {m.flagged_total} facts flagged.")
    head.extend(line for line in m.kb_lines if line)
    body: List[str] = []
    if m.too_large:
        body.append(TOO_LARGE)
    delivered = [s for s in m.sections if s.artefacts]
    if delivered:
        body.append(DELIVER_HEAD if m.deliver else NO_DELIVER_HEAD)
    for i, s in enumerate(m.sections, 1):
        body.append(f'View {i} of {n}: "{s.title}" ({s.view_id})')
        if s.missing:
            body.append(f"- not delivered ({s.missing})")
            continue
        if s.draft:
            body.append(f"- DRAFT: quality {s.score:.1f}/100, below the {s.threshold:.0f} pass "
                        f"mark; the names end in -draft")
            body.extend(s.hard_lines[:MAX_QUALITY_LINES])
        verdict = "pass" if s.passed else "below the pass mark"
        body.append(f"- Quality {s.score:.1f} of 100, {verdict} (threshold {s.threshold:.0f}); "
                    f"{s.nodes} nodes, {s.edges} edges, {s.groups} groups, {s.context} context "
                    f"elements")
        body += [f"- {f} ({b} B, sha256 {h})" for f, b, h in s.artefacts]
        body += [f"- Not delivered: {f} ({r})" for f, r in s.omitted]
    if delivered and not m.deliver:
        body.append(NO_DELIVER_HINT)
    flagged = _section("Flagged ({shown} of {total} shown; full list in " + m.report_hint + "):",
                       m.flagged_lines, MAX_FLAGGED_LINES, " (run ./run.sh diagram --check-facts)")
    generic: List[str] = []
    if m.generic_icons:
        shown = ", ".join(f'"{lab}"' for lab, _ in m.generic_icons[:4])
        more = f", and {len(m.generic_icons) - 4} more" if len(m.generic_icons) > 4 else ""
        codes = "/".join(sorted({c for _, c in m.generic_icons}))
        generic.append(f"Generic icon used for {shown}{more} (WARN {codes})")

    def assemble(fl: List[str], trunc: bool) -> str:
        parts = head + body + fl + generic + list(m.notes)
        if trunc:
            parts.append(TRUNCATED_LINE)
        return "\n".join(parts)

    text = assemble(flagged, False)
    trunc = False
    while len(text.encode("utf-8")) > ENGINE_TEXT_CAP and flagged:
        trunc = True
        flagged = flagged[:-1]
        text = assemble(flagged, True)
    if len(text.encode("utf-8")) > ENGINE_TEXT_CAP:
        logger.error("diagram: multi-view text over the cap; failing closed")
        return FAIL_CLOSED, False
    return _fail_closed_check(text)
