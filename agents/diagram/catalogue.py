"""The diagram fact catalogue (design D1.2): every validated fact of every specialist, indexed
for natural-language retrieval. Built once per request (the Tier-B overlay can change).

Index sources (D1.2, N1, N9): label tokens (weight 1), aliases (weight 2), tags (weight 1) of
nodes and groups. Ids are never indexed. Label tokens are stemmed but not synonym-expanded;
aliases and tags are stemmed and synonym-expanded. A multi-word entry is a phrase key (its
stemmed tokens joined by one space). Per entity and key the weight is the maximum over the
sources that produce it, never the sum. df counts entities; idf = ln(1 + N / df) where N is the
number of nodes and groups.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .compose import collect_facts
from .facts import Fact, Flag
from .topics import Topic, merge_topics
from .vocabulary import Vocabulary, load_vocabulary, stem, tokens

KIND_PREFIX = {"node": "n", "group": "g", "edge": "e"}
_TERM_RE = re.compile(r"^[a-z0-9]{2,24}$")
_HAS_LETTER = re.compile(r"[a-z]")

W_LABEL = 1
W_ALIAS = 2
W_TAG = 1


def qid(fact: Fact) -> str:
    return f"{KIND_PREFIX[fact.kind]}:{fact.id}"


def is_term_token(t: str) -> bool:
    return bool(_TERM_RE.match(t)) and bool(_HAS_LETTER.search(t))


def entry_key(text: str, vocab: Vocabulary, *, synonyms: bool) -> str:
    """The index key of one label token, alias or tag: tokenize, stem, (synonyms)."""
    toks = [stem(t) for t in tokens(text)]
    if synonyms:
        toks = vocab.expand(toks)
    return " ".join(toks)


@dataclass
class Catalogue:
    facts: Dict[str, Fact]
    adj_out: Dict[str, List[Tuple[str, str]]]
    adj_in: Dict[str, List[Tuple[str, str]]]
    index: Dict[str, List[Tuple[str, int]]]
    presets: List[Topic]
    vocabulary: Vocabulary
    sha256: str
    flags: List[Flag] = field(default_factory=list)
    #: entity qid -> {index key: weight}
    entity_keys: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: stemmed tag key -> raw tags (every kind, edges included)
    tag_keys: Dict[str, Set[str]] = field(default_factory=dict)
    order: Dict[str, int] = field(default_factory=dict)

    # -- lookups -------------------------------------------------------------------------------
    @property
    def n_entities(self) -> int:
        return sum(1 for f in self.facts.values() if f.kind in ("node", "group"))

    def idf(self, key: str) -> float:
        df = len(self.index.get(key, ()))
        if df == 0:
            return 0.0
        return math.log(1 + self.n_entities / df)

    def nodes(self) -> List[str]:
        return [q for q, f in self.facts.items() if f.kind == "node"]

    def groups(self) -> List[str]:
        return [q for q, f in self.facts.items() if f.kind == "group"]

    def edges(self) -> List[str]:
        return [q for q, f in self.facts.items() if f.kind == "edge"]

    def endpoints(self, eq: str) -> Tuple[str, str]:
        f = self.facts[eq]
        return f"n:{f.src}", f"n:{f.dst}"

    def denied(self, eq: str) -> bool:
        return self.facts[eq].style == "denied"

    def tags(self, q: str) -> Set[str]:
        return set(self.facts[q].tags)

    def group_of(self, q: str) -> Optional[str]:
        f = self.facts[q]
        g = f.group if f.kind == "node" else f.parent
        return f"g:{g}" if g is not None and f"g:{g}" in self.facts else None

    def ancestors(self, q: str) -> List[str]:
        out: List[str] = []
        g = self.group_of(q)
        while g is not None and g not in out:
            out.append(g)
            g = self.group_of(g)
        return out

    def members(self, gq: str) -> List[str]:
        """Every node inside the group, at any depth, in fact order."""
        return [n for n in self.nodes() if gq in self.ancestors(n)]

    def preset_facts(self, preset_id: str) -> List[str]:
        return [q for q, f in self.facts.items() if preset_id in f.topics]

    def preset(self, preset_id: str) -> Optional[Topic]:
        return next((p for p in self.presets if p.id == preset_id), None)

    def edges_between(self, a: str, b: str) -> List[str]:
        return [e for e, w in self.adj_out.get(a, []) if w == b] + \
               [e for e, w in self.adj_out.get(b, []) if w == a]


def _hash(facts: Dict[str, Fact], presets: List[Topic], vocab: Vocabulary) -> str:
    doc = {
        "facts": [[q, list(f.rendered_attrs()), sorted(f.sources), list(f.aliases), list(f.tags),
                   f.description, list(f.topics)] for q, f in sorted(facts.items())],
        "presets": [[p.id, p.title, list(p.triggers), p.suggestion] for p in presets],
        "vocabulary": vocab.canonical,
    }
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_catalogue(own: object, peers: Dict[str, object]) -> Catalogue:
    """Collect every fact (topic=None), then index it. Never raises on KB content."""
    knowledge = getattr(own, "_knowledge", {}) or {}
    collected = collect_facts(own, peers, topic=None)
    presets, pflags = merge_topics(knowledge)
    vocab, vflags = load_vocabulary(knowledge)
    facts: Dict[str, Fact] = {}
    for f in collected.facts:
        facts[qid(f)] = f
    used_tags: Set[str] = {t for f in facts.values() for t in f.tags}
    tflags = vocab.check_tags(used_tags)
    adj_out: Dict[str, List[Tuple[str, str]]] = {n: [] for n, f in facts.items()
                                                  if f.kind == "node"}
    adj_in: Dict[str, List[Tuple[str, str]]] = {n: [] for n in adj_out}
    for q, f in facts.items():
        if f.kind != "edge" or f.style == "denied":
            continue
        s, d = f"n:{f.src}", f"n:{f.dst}"
        adj_out[s].append((q, d))
        adj_in[d].append((q, s))
    entity_keys: Dict[str, Dict[str, int]] = {}
    tag_keys: Dict[str, Set[str]] = {}
    for q, f in facts.items():
        for t in f.tags:
            k = entry_key(t, vocab, synonyms=True)
            if k:
                tag_keys.setdefault(k, set()).add(t)
        if f.kind == "edge":
            continue
        keys: Dict[str, int] = {}

        def put(k: str, w: int) -> None:
            if k and all(is_term_token(p) for p in k.split(" ")):
                keys[k] = max(keys.get(k, 0), w)

        for tok in tokens(f.label):
            put(stem(tok), W_LABEL)
        for a in f.aliases:
            put(entry_key(a, vocab, synonyms=True), W_ALIAS)
        for t in f.tags:
            put(entry_key(t, vocab, synonyms=True), W_TAG)
        entity_keys[q] = keys
    index: Dict[str, List[Tuple[str, int]]] = {}
    for q, keys in entity_keys.items():
        for k, w in keys.items():
            index.setdefault(k, []).append((q, w))
    order = {q: i for i, q in enumerate(facts)}
    return Catalogue(facts=facts, adj_out=adj_out, adj_in=adj_in, index=index, presets=presets,
                     vocabulary=vocab, sha256=_hash(facts, presets, vocab),
                     flags=list(collected.flags) + pflags + vflags + tflags,
                     entity_keys=entity_keys, tag_keys=tag_keys, order=order)
