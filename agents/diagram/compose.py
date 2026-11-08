"""Traceable scene composition (design D4.4, D5.2). Every element is built through SceneElement,
which refuses an element with no source: "no untraced element" is owned here."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import labels as L
from .facts import Fact, Flag, merge, resolve_references, validate_fact_all
from .limits import MAX_FACTS, Limits

logger = logging.getLogger(__name__)


class SceneElement:
    """One rendered group, node or edge. Raises ValueError when `sources` is empty."""

    __slots__ = ("kind", "id", "label", "sources", "icon", "type", "parent", "src", "dst",
                 "optional", "style", "bidirectional", "kb_refs", "refs", "col", "row", "align")

    def __init__(self, kind: str, id: str, label: str, sources: List[str], *, icon: str = "",
                 type: str = "", parent: Optional[str] = None, src: str = "", dst: str = "",
                 optional: bool = False, style: str = "data", bidirectional: bool = False,
                 kb_refs: Optional[List[str]] = None, refs: Optional[List[str]] = None,
                 col: Optional[int] = None, row: Optional[int] = None,
                 align: Optional[str] = None):
        if not sources:
            raise ValueError("a scene element must cite at least one source")
        self.kind, self.id, self.label, self.sources = kind, id, label, list(sources)
        self.icon, self.type, self.parent = icon, type, parent
        self.src, self.dst, self.optional = src, dst, optional
        self.style, self.bidirectional = style, bidirectional
        self.kb_refs = list(kb_refs or [])
        self.refs = list(refs or [])
        self.col, self.row, self.align = col, row, align


@dataclass
class Scene:
    topic_id: str
    topic_title: str
    title: str
    groups: List[SceneElement]
    nodes: List[SceneElement]
    edges: List[SceneElement]
    flagged: List[Flag] = field(default_factory=list)
    source_keys: List[str] = field(default_factory=list)

    @property
    def rendered(self) -> int:
        return len(self.groups) + len(self.nodes) + len(self.edges)

    @property
    def cited(self) -> int:
        return sum(1 for e in self.groups + self.nodes + self.edges if e.sources)


@dataclass
class Collected:
    facts: List[Fact]
    flags: List[Flag]


def collect_facts(own: object, peers: Dict[str, object], topic: Optional[str] = None
                  ) -> Collected:
    """Own facts first, then each peer in registry order, through the read-only diagram_facts().
    `topic=None` collects every fact (the catalogue, D1.2). A peer with an invalid name is
    skipped and flagged without naming it; a peer that raises is skipped and flagged; a peer
    without the method is skipped silently."""
    raw: List[Tuple[str, dict]] = []
    flags: List[Flag] = []
    agents = [(getattr(own, "name", "diagram"), own)] + list(peers.items())
    for name, agent in agents:
        fn = getattr(agent, "diagram_facts", None)
        if not callable(fn):
            continue
        if not L.check_specialist_name(name):
            flags.append(Flag("skipped specialist", "specialist-name"))
            continue
        try:
            entries = fn(topic) or []
        except Exception as e:  # a peer failure is never fatal
            logger.warning("diagram: peer %s facts unavailable (%s)", name, type(e).__name__)
            flags.append(Flag("skipped specialist", "peer-unavailable", name))
            continue
        for entry in entries:
            if isinstance(entry, dict):
                raw.append((name, entry))
    if len(raw) > MAX_FACTS:
        flags.append(Flag("ignored", "fact-cap"))
        raw = raw[:MAX_FACTS]
    facts: List[Fact] = []
    for i, (name, entry) in enumerate(raw):
        kb_key = entry.get("kb_key", "")
        fact, fflags = validate_fact_all(entry, specialist=name, kb_key=kb_key, order=i)
        flags.extend(fflags)
        if fact:
            facts.append(fact)
    facts, mflags = merge(facts)
    facts, rflags = resolve_references(facts)
    return Collected(facts, flags + mflags + rflags)


@dataclass
class ComposeResult:
    scene: Optional[Scene]
    decline: str = ""       # "" | "bounds" | "few-nodes" | "no-facts"
    detail: str = ""


class UntracedElement(Exception):
    """A rendered element none of whose sources resolves to a loaded KB fact (design D5.1).
    Fatal for the request: the specialist returns the layout-error decline, never a partial
    artefact. The message names the element kind only (no label, no source)."""


_KIND_PREFIX = {"node": "n", "group": "g", "edge": "e"}


def compose(topic_id: str, topic_title: str, title: str, collected: Collected,
            limits: Limits, *, select: Optional[Iterable[str]] = None,
            resolver: Optional[Callable[[str, str, str], bool]] = None) -> ComposeResult:
    """Build the scene for one view. Without `select` the view is the preset `topic_id` (every
    fact tagged with it); with `select` it is exactly those catalogue qids (`n:`/`g:`/`e:`).
    `resolver(source, kind, id)` (D5.1) must accept at least one source of every rendered
    element, else UntracedElement is raised (traceability below 1.0)."""
    if select is not None:
        wanted = set(select)
        facts = [f for f in collected.facts if f"{_KIND_PREFIX[f.kind]}:{f.id}" in wanted]
    else:
        facts = [f for f in collected.facts if topic_id in f.topics]
    if not facts:
        return ComposeResult(None, "no-facts")
    groups = {f.id: f for f in facts if f.kind == "group"}
    nodes = [f for f in facts if f.kind == "node"]
    node_ids = {n.id for n in nodes}
    keep_groups = set()
    for n in nodes:
        g = n.group
        while g is not None and g in groups and g not in keep_groups:
            keep_groups.add(g)
            g = groups[g].parent
    edges = [f for f in facts if f.kind == "edge" and f.src in node_ids and f.dst in node_ids]
    kept_groups = [g for g in facts if g.kind == "group" and g.id in keep_groups]
    for kind, n, cap in (("nodes", len(nodes), limits.max_nodes),
                         ("edges", len(edges), limits.max_edges),
                         ("groups", len(kept_groups), limits.max_groups)):
        if n > cap:
            logger.info("diagram: topic %s needs %d %s (limit %d)", topic_id, n, kind, cap)
            return ComposeResult(None, "bounds", f"{n} {kind}; the limit is {cap}")
    if len(nodes) < 2:
        return ComposeResult(None, "few-nodes")

    def el(f: Fact) -> SceneElement:
        return SceneElement(f.kind, f.id, f.label, f.sources, icon=f.icon, type=f.type,
                            parent=f.parent if f.kind == "group" else f.group, src=f.src,
                            dst=f.dst, optional=f.optional, style=f.style,
                            bidirectional=f.bidirectional,
                            kb_refs=[s.split(":", 1)[1] for s in f.sources],
                            refs=[f.ref] if f.ref else [], col=f.col, row=f.row,
                            align=f.align)

    scene = Scene(topic_id, topic_title, title, [el(g) for g in kept_groups],
                  [el(n) for n in nodes], [el(e) for e in edges], list(collected.flags))
    if resolver is not None:
        for e in scene.groups + scene.nodes + scene.edges:
            if not any(resolver(s, e.kind, e.id) for s in e.sources):
                logger.error("diagram: untraced %s in the scene; failing closed", e.kind)
                raise UntracedElement(e.kind)
    keys: List[str] = []
    for e in scene.groups + scene.nodes + scene.edges:
        for s in e.sources:
            k = s.split("#", 1)[0]
            if k not in keys:
                keys.append(k)
    scene.source_keys = keys
    return ComposeResult(scene)
