"""Diagram fact schema, validation and merge (design D4.2, D4.4).

Each failure drops that one fact and records a Flag with a fixed reason code. Labels are checked
with the label policy and the gate's content detectors, both on the raw label and on the exact
manifest line that will carry it (D5.3 rule 5).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import labels as L
from .limits import MAX_DEPTH
from .manifest import line_for
from .style import GROUP_RANK, GROUP_TYPES

logger = logging.getLogger(__name__)

_ICON_RE = re.compile(r"^[A-Za-z0-9]{2,64}$")
_TOPIC_RE = re.compile(r"^[a-z0-9-]{1,48}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9 -]{1,31}$")
_TAG_RE = re.compile(r"^[a-z0-9-]{2,24}$")
MAX_FACT_TOPICS = 10
MAX_ALIASES = 8
MAX_TAGS = 8
MAX_DESCRIPTION = 120
#: Layout hints (node only): `col` places the node in that column of its container instead of
#: its rank; `row` orders the nodes of one column (lower first); `align` names another node whose
#: centre line this node is pushed down to (ignored when that node is not drawn). Hints never add
#: or drop an element and are never rendered.
MAX_HINT = 15


@dataclass
class Flag:
    """A dropped, skipped or conflicting item. `ref` is a validated `kb_key#id` (or kb_key, or a
    validated specialist name) or None; `reason` is a fixed code. Never carries a label."""
    action: str
    reason: str
    ref: Optional[str] = None

    def line(self) -> str:
        if self.ref:
            return f'- {self.action} "{self.ref}" (reason {self.reason})'
        return f"- {self.action} (reason {self.reason})"

    def as_dict(self) -> dict:
        return {"action": self.action, "kb_ref": self.ref, "reason": self.reason}


@dataclass
class Fact:
    kind: str
    id: str
    topics: List[str]
    kb_key: str
    specialist: str
    label: str = ""
    icon: str = "generic"
    type: str = ""
    parent: Optional[str] = None
    group: Optional[str] = None
    src: str = ""
    dst: str = ""
    optional: bool = False
    bidirectional: bool = False
    style: str = "data"
    ref: str = ""
    col: Optional[int] = None
    row: Optional[int] = None
    align: Optional[str] = None
    order: int = 0
    sources: List[str] = field(default_factory=list)
    # Retrieval metadata (D1.3): never rendered, never in rendered_attrs().
    aliases: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    description: str = ""
    #: names of metadata fields withheld from planners ("description", "aliases")
    withheld: List[str] = field(default_factory=list)

    @property
    def tier(self) -> str:
        return "a" if self.kb_key.startswith("tier_a/") else "b"

    @property
    def kb_ref(self) -> str:
        return f"{self.kb_key}#{self.id}"

    def rendered_attrs(self) -> tuple:
        if self.kind == "node":
            return (self.label, self.icon, self.group, self.optional, self.col, self.row,
                    self.align)
        if self.kind == "group":
            return (self.label, self.type, self.parent)
        return (self.src, self.dst, self.label, self.optional, self.style, self.bidirectional)


def _ref_of(kb_key: str, fid: object) -> str:
    if isinstance(fid, str) and L.FACT_ID_RE.match(fid):
        return f"{kb_key}#{fid}"
    return kb_key


def validate_fact(raw: dict, *, specialist: str, kb_key: str, order: int = 0
                  ) -> Tuple[Optional[Fact], Optional[Flag]]:
    """Schema + label policy for one raw entry from BaseAgent.diagram_facts(). Returns the fact
    (or None when dropped) and the first flag (the drop reason, or the first metadata flag of a
    kept fact). validate_fact_all returns every flag."""
    fact, flags = validate_fact_all(raw, specialist=specialist, kb_key=kb_key, order=order)
    return fact, (flags[0] if flags else None)


def _metadata(fact: Fact, raw: dict, ref: str) -> List[Flag]:
    """Retrieval metadata (D1.3). A bad alias, tag or description drops or withholds only that
    metadata, never the fact (metadata never renders)."""
    flags: List[Flag] = []
    aliases = raw.get("aliases")
    if aliases is not None:
        if (fact.kind == "edge" or not isinstance(aliases, list) or len(aliases) > MAX_ALIASES
                or not all(isinstance(a, str) and _ALIAS_RE.match(a) for a in aliases)):
            flags.append(Flag("ignored", "schema-aliases", ref))
        else:
            for a in aliases:
                if L.looks_like_instruction(a):
                    flags.append(Flag("withheld", "alias-injection", ref))
                    if "aliases" not in fact.withheld:
                        fact.withheld.append("aliases")
                elif a not in fact.aliases:
                    fact.aliases.append(a)
    tags = raw.get("tags")
    if tags is not None:
        if (not isinstance(tags, list) or len(tags) > MAX_TAGS
                or not all(isinstance(t, str) and _TAG_RE.match(t) for t in tags)):
            flags.append(Flag("ignored", "schema-tags", ref))
        else:
            fact.tags = list(dict.fromkeys(tags))
    desc = raw.get("description")
    if desc not in (None, ""):
        if isinstance(desc, str) and L.looks_like_instruction(desc):
            flags.append(Flag("withheld", "description-injection", ref))
            fact.withheld.append("description")
        elif (not isinstance(desc, str) or len(desc) > MAX_DESCRIPTION
              or L.check_label(desc, max_len=MAX_DESCRIPTION) is not None):
            flags.append(Flag("withheld", "description-policy", ref))
            fact.withheld.append("description")
        else:
            fact.description = desc
    return flags


def validate_fact_all(raw: dict, *, specialist: str, kb_key: str, order: int = 0
                      ) -> Tuple[Optional[Fact], List[Flag]]:
    """Schema + label policy for one raw entry. A dropped fact returns (None, [drop flag]); a
    kept fact returns (fact, metadata flags)."""
    if not L.check_kb_key(kb_key):
        return None, [Flag("dropped", "kb-key")]
    fid = raw.get("id")
    ref = _ref_of(kb_key, fid)

    def bad(reason: str):
        logger.debug("diagram fact dropped: %s (%s)", ref, reason)
        return None, [Flag("dropped", reason, ref)]

    kind = raw.get("kind")
    if kind not in ("node", "edge", "group"):
        return bad("schema-kind")
    if not isinstance(fid, str) or not L.FACT_ID_RE.match(fid):
        return bad("schema-id")
    topics = raw.get("topics", [])
    if topics is None:
        topics = []
    if (not isinstance(topics, list) or len(topics) > MAX_FACT_TOPICS
            or not all(isinstance(t, str) and _TOPIC_RE.match(t) for t in topics)):
        return bad("schema-topics")
    fref = raw.get("ref", "")
    if fref not in ("", None):
        if (not isinstance(fref, str) or not _REF_RE.match(fref) or ".." in fref
                or fref.startswith("/")):
            return bad("schema-ref")
    for b in ("optional", "bidirectional"):
        if b in raw and not isinstance(raw[b], bool):
            return bad("schema-bool")
    for h in ("col", "row"):
        if h in raw and (kind != "node" or isinstance(raw[h], bool) or not isinstance(raw[h], int)
                         or not 0 <= raw[h] <= MAX_HINT):
            return bad("schema-hint")
    if "align" in raw and (kind != "node" or not isinstance(raw["align"], str)
                           or not L.FACT_ID_RE.match(raw["align"]) or raw["align"] == fid):
        return bad("schema-hint")
    fact = Fact(kind=kind, id=fid, topics=list(topics), kb_key=kb_key, specialist=specialist,
                ref=fref or "", order=order, col=raw.get("col"), row=raw.get("row"),
                align=raw.get("align"))
    label = raw.get("label")
    if kind in ("node", "group") or label not in (None, ""):
        reason = L.check_label(label)
        if reason:
            return bad(reason)
        if L.looks_like_instruction(label):
            return bad("label-injection")
        fact.label = label
    if kind == "node":
        icon = raw.get("icon", "generic")
        if not isinstance(icon, str) or not _ICON_RE.match(icon):
            return bad("schema-icon")
        fact.icon = icon
        grp = raw.get("group")
        if grp is not None and (not isinstance(grp, str) or not L.FACT_ID_RE.match(grp)):
            return bad("schema-group")
        fact.group = grp
        fact.optional = bool(raw.get("optional", False))
    elif kind == "group":
        gtype = raw.get("type")
        if gtype not in GROUP_TYPES:
            return bad("schema-type")
        fact.type = gtype
        par = raw.get("parent")
        if par is not None and (not isinstance(par, str) or not L.FACT_ID_RE.match(par)):
            return bad("schema-parent")
        fact.parent = par
    else:
        src, dst = raw.get("from"), raw.get("to")
        if not all(isinstance(x, str) and L.FACT_ID_RE.match(x) for x in (src, dst)):
            return bad("schema-edge")
        if src == dst:
            return bad("self-loop")
        style = raw.get("style", "data")
        if style not in ("data", "denied"):
            return bad("schema-style")
        fact.src, fact.dst, fact.style = src, dst, style
        fact.optional = bool(raw.get("optional", False))
        fact.bidirectional = bool(raw.get("bidirectional", False))
    if fact.label:
        code = L.check_lines([line_for(_LineView(fact))])
        if code:
            return bad(code)
    source = f"{specialist}:{kb_key}#{fid}"
    if not L.check_source(source):
        return bad("source-detector")
    fact.sources = [source]
    return fact, _metadata(fact, raw, ref)


class _LineView:
    """Adapter so manifest.line_for can read a Fact."""

    def __init__(self, fact: Fact):
        self.kind, self.label = fact.kind, fact.label
        self.optional, self.style = fact.optional, fact.style


def merge(facts: List[Fact]) -> Tuple[List[Fact], List[Flag]]:
    """De-duplicate by kb_ref (first keeps the citation), then merge the same (kind, id):
    equal rendered attributes union sources; differing attributes: Tier-A beats Tier-B (both
    cited, conflict flagged); same-tier differences drop the element (never guess)."""
    flags: List[Flag] = []
    seen_ref = set()
    uniq: List[Fact] = []
    for f in facts:
        if f.kb_ref in seen_ref:
            continue
        seen_ref.add(f.kb_ref)
        uniq.append(f)
    by_key: Dict[Tuple[str, str], List[Fact]] = {}
    for f in uniq:
        by_key.setdefault((f.kind, f.id), []).append(f)
    out: List[Fact] = []
    for (_kind, _fid), group in by_key.items():
        first = group[0]
        if len(group) == 1:
            out.append(first)
            continue
        if all(g.rendered_attrs() == first.rendered_attrs() for g in group):
            for g in group[1:]:
                first.sources += [s for s in g.sources if s not in first.sources]
            out.append(first)
            continue
        tier_a = [g for g in group if g.tier == "a"]
        tier_b = [g for g in group if g.tier == "b"]
        a_attrs = {g.rendered_attrs() for g in tier_a}
        if tier_a and len(a_attrs) == 1 and tier_b:
            win = tier_a[0]
            for g in group:
                if g is win:
                    continue
                win.sources += [s for s in g.sources if s not in win.sources]
            for g in tier_b:
                if g.rendered_attrs() != win.rendered_attrs():
                    flags.append(Flag("conflict", "tier-precedence", g.kb_ref))
            out.append(win)
            continue
        for g in group:
            flags.append(Flag("dropped", "conflict", g.kb_ref))
    out.sort(key=lambda f: f.order)
    return out, flags


def resolve_references(facts: List[Fact]) -> Tuple[List[Fact], List[Flag]]:
    """Drop links to unknown groups, nesting violations, too-deep nesting, parent cycles, and
    edges whose endpoints are not nodes."""
    flags: List[Flag] = []
    groups = {f.id: f for f in facts if f.kind == "group"}
    for g in groups.values():
        if g.parent is None:
            continue
        p = groups.get(g.parent)
        if p is None:
            flags.append(Flag("dropped-link", "dangling-parent", g.kb_ref))
            g.parent = None
            continue
        cr, pr = GROUP_RANK.get(g.type), GROUP_RANK.get(p.type)
        if cr is not None and pr is not None and cr <= pr:
            flags.append(Flag("dropped-link", "nesting", g.kb_ref))
            g.parent = None
    # cycles and depth
    for g in sorted(groups.values(), key=lambda f: f.order):
        seen, cur, depth = {g.id}, g, 1
        while cur.parent is not None:
            nxt = groups.get(cur.parent)
            if nxt is None or nxt.id in seen:
                flags.append(Flag("dropped-link", "nesting", g.kb_ref))
                g.parent = None
                break
            seen.add(nxt.id)
            depth += 1
            if depth > MAX_DEPTH:
                flags.append(Flag("dropped-link", "nesting", g.kb_ref))
                g.parent = None
                break
            cur = nxt
    nodes = {f.id for f in facts if f.kind == "node"}
    out: List[Fact] = []
    for f in facts:
        if f.kind == "node" and f.group is not None and f.group not in groups:
            flags.append(Flag("dropped-link", "dangling-group", f.kb_ref))
            f.group = None
        if f.kind == "edge" and (f.src not in nodes or f.dst not in nodes):
            flags.append(Flag("dropped", "dangling-edge", f.kb_ref))
            continue
        out.append(f)
    return out, flags
