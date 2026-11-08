"""The deterministic natural-language planner (design D3.2, D3.2a, D3.3). LLM-free, ordered, and
reproducible: fact order breaks every tie. It turns a request into a Plan that only selects
catalogue ids; request text is reused only as sanitized terms (Tenet 4, Tenet 6)."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Union

from . import labels as L
from .catalogue import Catalogue, is_term_token
from .limits import MAX_REQUEST_CHARS, PATH_MAX_HOPS
from .plan import FLOW_SCOPE, MAX_FOCUS, Plan, title_for
from .vocabulary import Vocabulary, stem, tokens

# Re-exported so the planner owns the pinned stemmer by name (D3.2 step 4).
__all__ = ["Decline", "Normalized", "admissible", "normalize", "plan_deterministic", "stem"]

ANCHOR_RATIO = 0.6
MAX_ANCHORS = 6
MAX_UNMATCHED = 6
PATHS_PER_PAIR = 4
NEIGHBOURHOOD_HOPS = 4
ANCHOR_FREE_SCOPES = ("end-to-end", "security")


def _stopwords() -> Set[str]:
    from ..base_agent import BaseAgent
    return {stem(w) for w in BaseAgent._STOPWORDS}


@dataclass
class Normalized:
    safe_request: str
    plain: str
    scope: str = ""
    scope_phrases: List[str] = field(default_factory=list)
    presets: List[str] = field(default_factory=list)
    #: stemmed, synonym-expanded token segments (consumed spans split segments)
    segments: List[List[str]] = field(default_factory=list)
    #: distinct terms in request order
    terms: List[str] = field(default_factory=list)


@dataclass
class Decline:
    """No plan. `reason`: "no-anchor" (nothing named, or nothing that anchors) or "no-match"
    (named terms the knowledge base does not have)."""
    reason: str
    scope: str = ""
    unmatched: List[str] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)


def _phrase_re(phrase: str) -> "re.Pattern":
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<![a-z0-9])" + r"\s+".join(words) + r"(?![a-z0-9])")


@lru_cache(maxsize=4096)
def _term_ok(tok: str) -> bool:
    return is_term_token(tok) and L.check_label(tok) is None


def normalize(request: str, vocab: Vocabulary, presets: Sequence = ()) -> Normalized:
    """D3.2 in the pinned order: span removal -> lowercase ASCII -> longest-first scope and preset
    trigger matching on the unstemmed text (matched spans are consumed) -> tokenize -> stem ->
    synonyms -> drop stopwords and ignore words. A token is a term when it is 2..24 of
    [a-z0-9] with a letter, passes the label policy and has no gate content finding."""
    request = request[:MAX_REQUEST_CHARS] if isinstance(request, str) else ""
    safe = L.strip_sensitive(request)
    plain = safe.lower().encode("ascii", "ignore").decode("ascii")
    cands: List[Tuple[str, str, str]] = [(p, "scope", s) for p, s in vocab.scopes]
    for t in presets:
        for trig in t.triggers:
            cands.append((trig, "preset", t.id))
    cands.sort(key=lambda c: (-len(c[0]), c[1], c[0]))
    consumed: List[Tuple[int, int]] = []
    norm = Normalized(safe_request=safe, plain=plain)
    best_scope: Tuple[int, str] = (0, "")
    for phrase, kind, value in cands:
        for m in _phrase_re(phrase).finditer(plain):
            a, b = m.span()
            if any(a < y and x < b for x, y in consumed):
                continue
            consumed.append((a, b))
            if kind == "scope":
                norm.scope_phrases.append(phrase)
                if len(phrase) > best_scope[0]:
                    best_scope = (len(phrase), value)
            elif value not in norm.presets:
                norm.presets.append(value)
    norm.scope = best_scope[1]
    order = {t.id: i for i, t in enumerate(presets)}
    norm.presets.sort(key=lambda p: order.get(p, 0))
    consumed.sort()
    pieces, pos = [], 0
    for a, b in consumed:
        pieces.append(plain[pos:a])
        pos = b
    pieces.append(plain[pos:])
    drop = _stopwords() | vocab.ignore
    for piece in pieces:
        toks = vocab.expand([stem(t) for t in tokens(piece)])
        seg = [t for t in toks if t not in drop and _term_ok(t)]
        if seg:
            norm.segments.append(seg)
            for t in seg:
                if t not in norm.terms:
                    norm.terms.append(t)
    return norm


# -- scoring -----------------------------------------------------------------------------------

def _phrase_in(segments: List[List[str]], ptoks: List[str]) -> bool:
    n = len(ptoks)
    return any(seg[i:i + n] == ptoks for seg in segments for i in range(len(seg) - n + 1))


def score(norm: Normalized, cat: Catalogue) -> Tuple[Dict[str, float], Set[str]]:
    """Per entity: the sum over matched phrases and distinct unconsumed terms of idf x weight
    (weight already the max per entity and key). Returns (scores, terms matched by a phrase)."""
    scores: Dict[str, float] = {}
    phrase_terms: Set[str] = set()
    for q, keys in cat.entity_keys.items():
        s = 0.0
        used: Set[str] = set()
        for k in sorted((k for k in keys if " " in k), key=lambda k: (-len(k), k)):
            ptoks = k.split(" ")
            if _phrase_in(norm.segments, ptoks):
                s += cat.idf(k) * keys[k]
                used.update(ptoks)
                phrase_terms.update(ptoks)
        for t in norm.terms:
            if t in used or t not in keys:
                continue
            s += cat.idf(t) * keys[t]
        if s > 0:
            scores[q] = s
    return scores, phrase_terms


def admissible(cat: Catalogue, eq: str, A: Iterable[str], B: Iterable[str] = (),
               anchors: Iterable[str] = ()) -> bool:
    """D3.2a: not denied, and it touches an anchor, or its tags meet A or B, or meet the
    connector tags, or it has no tags and is not optional."""
    f = cat.facts[eq]
    if f.style == "denied":
        return False
    anchors = set(anchors)
    if f"n:{f.src}" in anchors or f"n:{f.dst}" in anchors:
        return True
    tags = set(f.tags)
    if tags & (set(A) | set(B)):
        return True
    if tags & cat.vocabulary.role("connector_tags"):
        return True
    return not tags and not f.optional


def _undirected(cat: Catalogue, n: str) -> List[Tuple[str, str]]:
    both = list(cat.adj_out.get(n, [])) + list(cat.adj_in.get(n, []))
    return sorted(both, key=lambda ew: cat.order[ew[0]])


def shortest_paths(cat: Catalogue, src: str, dst: str, ok, max_hops: int = PATH_MAX_HOPS,
                   limit: int = PATHS_PER_PAIR) -> List[List[str]]:
    """All shortest undirected paths (edge qid lists) over edges accepted by `ok`, at most
    `max_hops` long, at most `limit` paths, in fact order."""
    if src == dst:
        return []
    dist = {src: 0}
    preds: Dict[str, List[Tuple[str, str]]] = {}
    q = deque([src])
    while q:
        u = q.popleft()
        if dist[u] >= max_hops or (dst in dist and dist[u] >= dist[dst]):
            continue
        for e, w in _undirected(cat, u):
            if not ok(e):
                continue
            if w not in dist:
                dist[w] = dist[u] + 1
                q.append(w)
            if dist[w] == dist[u] + 1:
                preds.setdefault(w, []).append((e, u))
    if dst not in dist:
        return []
    out: List[List[str]] = []

    def back(v: str, acc: List[str]) -> None:
        if len(out) >= limit:
            return
        if v == src:
            out.append(list(reversed(acc)))
            return
        for e, u in preds.get(v, []):
            back(u, acc + [e])

    back(dst, [])
    return out


def _walk(cat: Catalogue, start: str, direction: str, A: Set[str], anchors: Set[str],
          sel_nodes: Set[str], sel_edges: Set[str], first_tags: Set[str] = frozenset()) -> None:
    """Walk from `start` (bounded, through transit nodes). `first_tags`, when set, admits only a
    first hop that carries one of those tags (the provider walk of neighbourhood-out)."""
    transit = cat.vocabulary.role("transit_tags")
    seen = {(start, frozenset())}
    frontier = deque([(start, frozenset(), 0)])
    while frontier:
        node, B, hops = frontier.popleft()
        if hops >= NEIGHBOURHOOD_HOPS:
            continue
        if direction == "in":
            nbrs = cat.adj_in.get(node, [])
        elif direction == "out":
            nbrs = cat.adj_out.get(node, [])
        else:
            nbrs = _undirected(cat, node)
        for e, other in sorted(nbrs, key=lambda ew: cat.order[ew[0]]):
            if hops == 0 and first_tags and not set(cat.facts[e].tags) & set(first_tags):
                continue
            if not admissible(cat, e, A, B, anchors):
                continue
            nb = B if hops > 0 else frozenset(cat.facts[e].tags)
            sel_edges.add(e)
            sel_nodes.add(other)
            if other not in anchors and cat.tags(other) & transit:
                key = (other, nb)
                if key not in seen:
                    seen.add(key)
                    frontier.append((other, nb, hops + 1))


def _select(cat: Catalogue, scope: str, anchors: List[str], A: Set[str],
            flow_tag: str = "") -> Tuple[Set[str], Set[str], Set[str]]:
    """(nodes, groups, edges) selected by the scope rule (D3.3 step 6)."""
    nodes: Set[str] = set()
    groups: Set[str] = set()
    edges: Set[str] = set()
    aset = set(anchors)
    ok = (lambda e: admissible(cat, e, A, (), aset))
    if scope == "end-to-end":
        return set(cat.nodes()), set(cat.groups()), set(cat.edges())
    if scope == "security":
        sec = cat.vocabulary.role("security_tags")
        for q in cat.nodes():
            if cat.tags(q) & sec:
                nodes.add(q)
        for e in cat.edges():
            if cat.tags(e) & sec or cat.denied(e):
                edges.add(e)
                nodes.update(cat.endpoints(e))
        return nodes, groups, edges
    if scope == FLOW_SCOPE:
        for e in cat.edges():
            if flow_tag in cat.facts[e].tags and not cat.denied(e):
                edges.add(e)
                nodes.update(cat.endpoints(e))
        return nodes, groups, edges
    nodes.update(anchors)
    if scope == "path":
        pairs: List[Tuple[str, str]] = []
        if len(anchors) >= 2:
            pairs = [(anchors[0], b) for b in anchors[1:]]
        elif len(anchors) == 1:
            entry = cat.vocabulary.role("entry_tags")
            pairs = [(n, anchors[0]) for n in cat.nodes()
                     if n != anchors[0] and cat.tags(n) & entry]
        for a, b in pairs:
            for path in shortest_paths(cat, a, b, ok):
                for e in path:
                    edges.add(e)
                    nodes.update(cat.endpoints(e))
    elif scope == "path-in":
        for a in anchors:
            dist = {a: 0}
            q = deque([a])
            while q:
                v = q.popleft()
                if dist[v] >= PATH_MAX_HOPS:
                    continue
                for e, u in sorted(cat.adj_in.get(v, []), key=lambda ew: cat.order[ew[0]]):
                    if not ok(e):
                        continue
                    edges.add(e)
                    nodes.add(u)
                    if u not in dist:
                        dist[u] = dist[v] + 1
                        q.append(u)
    elif scope in ("neighbourhood-in", "neighbourhood-out"):
        direction = "in" if scope == "neighbourhood-in" else "out"
        for a in anchors:
            _walk(cat, a, direction, A, aset, nodes, edges)
        providers = cat.vocabulary.role("provider_tags")
        if direction == "out" and providers:
            # arrows follow the data, so what a node depends on for data it reads arrives as an
            # incoming arrow; only the vocabulary's provider_tags (shipped: storage) are walked
            for a in anchors:
                _walk(cat, a, "in", A, aset, nodes, edges, first_tags=providers)
        if direction == "in" and not edges:
            for a in anchors:
                _walk(cat, a, "both", A, aset, nodes, edges)
    else:  # focus
        for a in anchors:
            for e, w in _undirected(cat, a):
                if ok(e):
                    edges.add(e)
                    nodes.add(w)
    return nodes, groups, edges


def active_tags(cat: Catalogue, anchor_entities: Iterable[str], terms: Iterable[str]) -> Set[str]:
    """A: the anchors' tags plus every catalogue tag (of any kind) equal to a request term."""
    A: Set[str] = set()
    for q in anchor_entities:
        A |= cat.tags(q)
    for t in terms:
        A |= cat.tag_keys.get(t, set())
    return A


def plan_deterministic(norm: Normalized, cat: Catalogue,
                       caller_title: str = "") -> Union[Plan, Decline]:
    """D3.3 steps 1 to 6."""
    scores, phrase_terms = score(norm, cat)
    # step 4 (computed first: it does not depend on anchors) - unmatched terms (M2)
    unmatched = [t for t in norm.terms
                 if t not in cat.index and t not in phrase_terms and t not in cat.tag_keys]
    unmatched = unmatched[:MAX_UNMATCHED]
    # step 2 - anchors
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], cat.order[kv[0]]))
    best = ranked[0][1] if ranked else 0.0
    entity_anchors = [q for q, s in ranked if best > 0 and s >= ANCHOR_RATIO * best - 1e-9]
    entity_anchors = entity_anchors[:MAX_ANCHORS]
    node_anchors = [q for q in entity_anchors if q.startswith("n:")]
    anchors: List[str] = list(node_anchors)
    for g in (q for q in entity_anchors if q.startswith("g:")):
        if not any(scores[n] >= scores[g] for n in node_anchors):
            for m in cat.members(g):
                if m not in anchors:
                    anchors.append(m)
    # step 1 - preset
    if len(norm.presets) == 1:
        pid = norm.presets[0]
        pfacts = set(cat.preset_facts(pid))
        if cat.preset(pid) is not None and pfacts and all(q in pfacts for q in entity_anchors):
            plan = Plan(selected=[q for q in cat.facts if q in pfacts], scope="focus",
                        preset=pid, unmatched=unmatched, planner="preset", terms=list(norm.terms))
            plan.title = title_for(plan, cat, norm.terms, caller_title)
            return plan
    # step 3 - scope. A request made only of ignored words ("draw our architecture", "diagram
    # gac") names no component, no scope and no unknown term: it asks for the whole picture.
    scope = norm.scope or ("focus" if anchors else "")
    if not anchors and not norm.scope and not norm.terms and not norm.presets:
        scope = "end-to-end"
    A = active_tags(cat, entity_anchors + [a for a in anchors if a not in entity_anchors],
                    norm.terms)
    # step 5 - zero anchors
    flow_tag = ""
    if not anchors and scope not in ANCHOR_FREE_SCOPES:
        if not norm.scope:
            flows = [t for term in norm.terms for t in sorted(cat.tag_keys.get(term, ()))
                     if t in cat.vocabulary.flow_order]
            flow_tag = flows[0] if flows else ""
        if not flow_tag:
            return Decline("no-match" if unmatched else "no-anchor", scope=norm.scope,
                           unmatched=unmatched, terms=list(norm.terms))
        scope = FLOW_SCOPE
    # step 6 - selection
    nodes, groups, edges = _select(cat, scope, anchors, A, flow_tag)
    chosen = nodes | groups | edges
    plan = Plan(selected=[q for q in cat.facts if q in chosen], scope=scope,
                focus=anchors[:MAX_FOCUS], unmatched=unmatched, planner="deterministic",
                active=sorted(A), flow=flow_tag, terms=list(norm.terms))
    plan.title = title_for(plan, cat, norm.terms, caller_title)
    return plan


def plan_request(request: str, cat: Catalogue, caller_title: str = "") -> Union[Plan, Decline]:
    """Normalize then plan (a convenience for the specialist and tests)."""
    return plan_deterministic(normalize(request, cat.vocabulary, cat.presets), cat, caller_title)
