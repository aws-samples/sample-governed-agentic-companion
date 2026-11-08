"""Diagram topics: schema, Tier-A/Tier-B merge and deterministic matching (design D4.2, D5.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import labels as L
from .facts import Flag
from .limits import MAX_REQUEST_CHARS, MAX_TOPICS

_TOPIC_ID = re.compile(r"^[a-z0-9-]{1,48}$")


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    triggers: Tuple[str, ...]
    kb_key: str
    #: a curated prompt that replays to this preset (M8); default "draw the <triggers[0]>"
    suggestion: str = ""


def _suggestion(e: dict, triggers: List[str], ref: str, flags: List[Flag]) -> str:
    """The preset's suggested prompt: the optional `suggestion` key (label policy + injection
    detector), else "draw the " + triggers[0]. A rejected value is flagged and replaced by the
    default; a default that fails the same checks gives no suggestion."""
    raw = e.get("suggestion")
    if raw is not None:
        if (isinstance(raw, str) and L.check_label(raw) is None
                and not L.looks_like_instruction(raw)):
            return raw
        flags.append(Flag("ignored", "topic-suggestion", ref))
    default = "draw the " + triggers[0]
    if L.check_label(default) is None and not L.looks_like_instruction(default):
        return default
    return ""


def _valid_trigger(t: object) -> bool:
    return isinstance(t, str) and 3 <= len(t) <= 60 and t == t.lower()


def merge_topics(knowledge: Dict[str, dict]) -> Tuple[List[Topic], List[Flag]]:
    """Read every `tier_a/diagram/**/topics` then `tier_b/diagram/**/topics` key (sorted). On a
    repeated id the first wins (Tier-A, else the earlier Tier-B key) and the later is flagged."""
    keys = [k for k in knowledge
            if k.startswith(("tier_a/diagram/", "tier_b/diagram/")) and k.rsplit("/", 1)[-1] == "topics"]
    keys.sort(key=lambda k: (0 if k.startswith("tier_a/") else 1, k))
    out: List[Topic] = []
    flags: List[Flag] = []
    seen = set()
    for key in keys:
        if not L.check_kb_key(key):
            flags.append(Flag("ignored", "kb-key"))
            continue
        data = knowledge.get(key)
        entries = data.get("diagram_topics") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        for e in entries:
            tid = e.get("id") if isinstance(e, dict) else None
            ref = f"{key}#{tid}" if isinstance(tid, str) and _TOPIC_ID.match(tid) else key
            if not isinstance(e, dict) or not isinstance(tid, str) or not _TOPIC_ID.match(tid):
                flags.append(Flag("ignored", "topic-schema", ref))
                continue
            title = e.get("title")
            triggers = e.get("triggers")
            if L.check_label(title) is not None:
                flags.append(Flag("ignored", "topic-label", ref))
                continue
            if L.looks_like_instruction(title):
                flags.append(Flag("ignored", "preset-injection", ref))
                continue
            if (not isinstance(triggers, list) or not 1 <= len(triggers) <= 20
                    or not all(_valid_trigger(t) for t in triggers)):
                flags.append(Flag("ignored", "topic-schema", ref))
                continue
            if tid in seen:
                flags.append(Flag("ignored", "topic-duplicate", ref))
                continue
            if len(out) >= MAX_TOPICS:
                flags.append(Flag("ignored", "topic-cap", ref))
                continue
            seen.add(tid)
            out.append(Topic(tid, title, tuple(triggers), key,
                             _suggestion(e, triggers, ref, flags)))
    return out, flags


def match_topic(request: str, topics: List[Topic]) -> Tuple[Optional[Topic], str]:
    """Highest trigger score wins; score 0 or a tie at the top is no match. Returns
    (topic, "") or (None, "too-long" | "no-match" | "tie")."""
    if len(request) > MAX_REQUEST_CHARS:
        return None, "too-long"
    text = request.lower()
    scored = []
    for t in topics:
        score = sum(1 for trig in t.triggers if trig in text)
        if t.id in text or t.title.lower() in text:
            score += 1
        scored.append((score, t))
    best = max((s for s, _ in scored), default=0)
    if best == 0:
        return None, "no-match"
    winners = [t for s, t in scored if s == best]
    if len(winners) > 1:
        return None, "tie"
    return winners[0], ""
