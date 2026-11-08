"""KB-owned diagram vocabulary (design D3.1): synonyms, ignore words, scope phrases, flow order
and roles. Loaded from `tier_a/diagram/**/vocabulary` then `tier_b/diagram/**/vocabulary`;
Tier-B adds entries, Tier-A wins a conflicting key (the Tier-B entry is flagged). Every invalid
entry is flagged with a fixed code and skipped. Keys and values of synonyms and ignore words are
stemmed at load with the same `stem()` the planner applies to a request (D3.2).

The text primitives (`tokens`, `stem`) live here so the catalogue, the vocabulary and the
deterministic planner share one implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import labels as L
from .facts import Flag

SCOPES = ("focus", "path", "path-in", "neighbourhood-in", "neighbourhood-out", "security",
          "end-to-end")
ROLE_KEYS = ("transit_tags", "connector_tags", "security_tags", "entry_tags",
             "provider_tags")
MAX_SYNONYMS = 200
MAX_IGNORE = 150
MAX_SCOPES = 100
MAX_NOTE = 120

_ENTRY_RE = re.compile(r"^[a-z0-9][a-z0-9 -]{0,31}$")
_TAG_RE = re.compile(r"^[a-z0-9-]{2,24}$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_IRREGULAR = {"written": "write", "wrote": "write"}


# -- text primitives (D3.2 steps 3 and 4) -------------------------------------------------------

def tokens(text: str) -> List[str]:
    """Lowercase `[a-z0-9]+` tokens of an ASCII-lowered string."""
    plain = text.lower().encode("ascii", "ignore").decode("ascii")
    return _TOKEN_RE.findall(plain)


def stem(token: str) -> str:
    """The pinned stemmer (D3.2 step 4). The first rule that fits, in order: strip `ing`; strip
    `ed`; strip `es` only after s, x, z, ch or sh; strip a final `s`. A rule fits only when the
    stem left is at least 4 characters. Then the irregular map (written, wrote -> write)."""
    t = token
    if t.endswith("ing") and len(t) - 3 >= 4:
        t = t[:-3]
    elif t.endswith("ed") and len(t) - 2 >= 4:
        t = t[:-2]
    elif (t.endswith("es") and len(t) - 2 >= 4
          and t[:-2].endswith(("s", "x", "z", "ch", "sh"))):
        t = t[:-2]
    elif t.endswith("s") and len(t) - 1 >= 4:
        t = t[:-1]
    return _IRREGULAR.get(t, t)


def stem_all(words: Iterable[str]) -> Tuple[str, ...]:
    return tuple(stem(w) for w in words)


# -- the vocabulary -----------------------------------------------------------------------------

@dataclass
class Vocabulary:
    summary: str = ""
    topology_note: str = ""
    #: (stemmed key tokens, stemmed value tokens), longest key first
    synonyms: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = field(default_factory=list)
    #: stemmed ignore words
    ignore: Set[str] = field(default_factory=set)
    #: (unstemmed phrase, scope), longest phrase first
    scopes: List[Tuple[str, str]] = field(default_factory=list)
    flow_order: List[str] = field(default_factory=list)
    roles: Dict[str, List[str]] = field(default_factory=lambda: {k: [] for k in ROLE_KEYS})
    #: the validated entries as loaded (for the catalogue hash)
    canonical: dict = field(default_factory=dict)

    def role(self, key: str) -> Set[str]:
        return set(self.roles.get(key, []))

    def expand(self, toks: Sequence[str]) -> List[str]:
        """Apply synonyms longest phrase first, left to right, on a stemmed token sequence."""
        out: List[str] = []
        i = 0
        while i < len(toks):
            for key, value in self.synonyms:
                n = len(key)
                if tuple(toks[i:i + n]) == key:
                    out.extend(value)
                    i += n
                    break
            else:
                out.append(toks[i])
                i += 1
        return out

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical, sort_keys=True).encode()).hexdigest()

    def check_tags(self, used: Set[str]) -> List[Flag]:
        """Drop flow_order and role tags no fact uses (flag `vocabulary-unknown-tag`)."""
        flags: List[Flag] = []
        kept = [t for t in self.flow_order if t in used]
        if len(kept) != len(self.flow_order):
            flags.append(Flag("ignored", "vocabulary-unknown-tag"))
        self.flow_order = kept
        for k in ROLE_KEYS:
            vals = self.roles.get(k, [])
            kept = [t for t in vals if t in used]
            if len(kept) != len(vals):
                flags.append(Flag("ignored", "vocabulary-unknown-tag"))
            self.roles[k] = kept
        self.canonical["flow_order"] = list(self.flow_order)
        self.canonical["roles"] = {k: list(v) for k, v in self.roles.items()}
        return flags


def _vocab_keys(knowledge: Dict[str, dict]) -> List[str]:
    keys = [k for k in knowledge
            if k.startswith(("tier_a/diagram/", "tier_b/diagram/"))
            and k.rsplit("/", 1)[-1] == "vocabulary"]
    keys.sort(key=lambda k: (0 if k.startswith("tier_a/") else 1, k))
    return keys


def _entry_ok(s: object) -> bool:
    return isinstance(s, str) and bool(_ENTRY_RE.match(s))


def load_vocabulary(knowledge: Dict[str, dict]) -> Tuple[Vocabulary, List[Flag]]:
    """Merge and validate every vocabulary key. Never raises."""
    v = Vocabulary()
    flags: List[Flag] = []
    syn: Dict[str, str] = {}
    syn_tier: Dict[str, str] = {}
    ignore: List[str] = []
    scopes: Dict[str, str] = {}
    scope_tier: Dict[str, str] = {}
    flow: List[str] = []
    roles: Dict[str, List[str]] = {k: [] for k in ROLE_KEYS}
    note: Optional[str] = None
    summary: Optional[str] = None
    for key in _vocab_keys(knowledge):
        if not L.check_kb_key(key):
            flags.append(Flag("ignored", "kb-key"))
            continue
        tier = key[:6]
        data = knowledge.get(key)
        if not isinstance(data, dict):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            continue
        if summary is None and isinstance(data.get("summary"), str):
            summary = data["summary"]
        tn = data.get("topology_note")
        if tn is not None:
            if (isinstance(tn, str) and len(tn) <= MAX_NOTE
                    and L.check_label(tn, max_len=MAX_NOTE) is None
                    and not L.looks_like_instruction(tn)):
                if note is None:
                    note = tn
                else:
                    flags.append(Flag("ignored", "vocabulary-conflict", key))
            else:
                flags.append(Flag("ignored", "vocabulary-schema", key))
        raw_syn = data.get("synonyms") or {}
        if not isinstance(raw_syn, dict):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            raw_syn = {}
        for k, val in raw_syn.items():
            if not (_entry_ok(k) and _entry_ok(val)):
                flags.append(Flag("ignored", "vocabulary-schema", key))
                continue
            if k in syn:
                if syn[k] != val:
                    flags.append(Flag("ignored", "vocabulary-conflict", key))
                continue
            if len(syn) >= MAX_SYNONYMS:
                flags.append(Flag("ignored", "vocabulary-cap", key))
                continue
            syn[k], syn_tier[k] = val, tier
        raw_ign = data.get("ignore") or []
        if not isinstance(raw_ign, list):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            raw_ign = []
        for w in raw_ign:
            if not _entry_ok(w):
                flags.append(Flag("ignored", "vocabulary-schema", key))
                continue
            if w in ignore:
                continue
            if len(ignore) >= MAX_IGNORE:
                flags.append(Flag("ignored", "vocabulary-cap", key))
                continue
            ignore.append(w)
        raw_sc = data.get("scopes") or {}
        if not isinstance(raw_sc, dict):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            raw_sc = {}
        for k, val in raw_sc.items():
            if not _entry_ok(k) or val not in SCOPES:
                flags.append(Flag("ignored", "vocabulary-schema", key))
                continue
            if k in scopes:
                if scopes[k] != val:
                    flags.append(Flag("ignored", "vocabulary-conflict", key))
                continue
            if len(scopes) >= MAX_SCOPES:
                flags.append(Flag("ignored", "vocabulary-cap", key))
                continue
            scopes[k], scope_tier[k] = val, tier
        raw_flow = data.get("flow_order") or []
        if not isinstance(raw_flow, list):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            raw_flow = []
        for t in raw_flow:
            if not (isinstance(t, str) and _TAG_RE.match(t)):
                flags.append(Flag("ignored", "vocabulary-schema", key))
            elif t not in flow:
                flow.append(t)
        raw_roles = data.get("roles") or {}
        if not isinstance(raw_roles, dict):
            flags.append(Flag("ignored", "vocabulary-schema", key))
            raw_roles = {}
        for rk, vals in raw_roles.items():
            if rk not in ROLE_KEYS or not isinstance(vals, list):
                flags.append(Flag("ignored", "vocabulary-schema", key))
                continue
            for t in vals:
                if not (isinstance(t, str) and _TAG_RE.match(t)):
                    flags.append(Flag("ignored", "vocabulary-schema", key))
                elif t not in roles[rk]:
                    roles[rk].append(t)
    # A synonym whose value is a scope phrase can never work: scopes match before synonyms (N10).
    for k in list(syn):
        if syn[k] in scopes:
            flags.append(Flag("ignored", "vocabulary-synonym-scope"))
            del syn[k]
    v.summary = summary or ""
    v.topology_note = note or ""
    v.synonyms = sorted(((stem_all(tokens(k)), stem_all(tokens(val))) for k, val in syn.items()),
                        key=lambda kv: (-len(kv[0]), kv[0]))
    v.synonyms = [kv for kv in v.synonyms if kv[0]]
    v.ignore = {stem(t) for w in ignore for t in tokens(w)}
    v.scopes = sorted(scopes.items(), key=lambda kv: (-len(kv[0]), kv[0]))
    v.flow_order = flow
    v.roles = roles
    v.canonical = {"topology_note": v.topology_note, "synonyms": dict(sorted(syn.items())),
                   "ignore": sorted(ignore), "scopes": dict(sorted(scopes.items())),
                   "flow_order": list(flow), "roles": {k: list(r) for k, r in roles.items()}}
    return v, flags
