"""Label policy for every rendered string (design D5.3, Tenet 6).

A label that fails is never redacted-and-rendered (a redacted node would mislead): the element is
dropped and a flagged item with a fixed, secret-free reason code is recorded.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .limits import MAX_LABEL, SOURCE_MAX_BYTES

def _charset(max_len: int) -> "re.Pattern":
    return re.compile(r"^[A-Za-z0-9 ()/:.,+&_'-]{1,%d}$" % max_len)


_CHARSET = _charset(MAX_LABEL)
_IDENTIFIERS = [
    re.compile(r"\d{12}"),
    re.compile(r"(?i)arn:"),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"(?i)\b(?:vpc|subnet|sg|vpce|i|eni|rtb|igw|nat)-[0-9a-f]{8,17}\b"),
    re.compile(r"(?i)\b[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+"),
    re.compile(r"@"),
]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SPECIALIST_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
KB_KEY_RE = re.compile(r"^tier_[ab]/[a-z0-9_][a-z0-9_/-]{0,95}$")
FACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_T6_WORDS = ("password", "passwd", "secret", "token")


def _gate():
    from ..governance_gate import get_gate
    return get_gate()


def gate_block_code(text: str) -> Optional[str]:
    """First BLOCK finding code from the gate's pure content detectors (T1, T3, T6, T11)."""
    g = _gate()
    for f in g.content_findings(text) + g.content_findings_t11(text):
        if f.severity.value == "block":
            return f.code
    return None


def check_label(label: object, max_len: int = MAX_LABEL) -> Optional[str]:
    """Return None when the label may be rendered, else a fixed reason code. Rules 1, 2, 3, 5.
    `max_len` lets a longer metadata string (a fact description, 120) use the same policy."""
    if not isinstance(label, str) or not label:
        return "label-missing"
    from ..data_protection import scrub_or_reject
    res = scrub_or_reject(label, stage="diagram-label", actor="diagram")
    if res.findings or not res.ok:
        return "label-policy"
    charset = _CHARSET if max_len == MAX_LABEL else _charset(max_len)
    if not charset.match(label):
        return "label-charset"
    for pat in _IDENTIFIERS:
        if pat.search(label):
            return "label-identifier"
    code = gate_block_code(label)
    if code:
        return f"gate-detector:{code}"
    return None


def check_name(name: object) -> bool:
    """Rule 4: the output file name slug."""
    return isinstance(name, str) and bool(NAME_RE.match(name))


def check_specialist_name(name: object) -> bool:
    """Rule 6: a registry name that may appear in a source string."""
    if not isinstance(name, str) or not SPECIALIST_RE.match(name):
        return False
    return not name.endswith(_T6_WORDS)


def check_kb_key(key: object) -> bool:
    return isinstance(key, str) and bool(KB_KEY_RE.match(key))


def check_source(source: str) -> bool:
    """Rule 6: an assembled source string is short and trips no gate content detector."""
    if len(source.encode("utf-8")) > SOURCE_MAX_BYTES:
        return False
    return gate_block_code(source) is None


def check_lines(lines: List[str]) -> Optional[str]:
    for line in lines:
        code = gate_block_code(line)
        if code:
            return f"gate-detector:{code}"
    return None


# -- planner hygiene (design D2.5, D3.2 step 1) --------------------------------------------------

#: The pinned, case-insensitive instruction detector (D2.5). Data hygiene for every string that
#: can reach a planner (labels, aliases, descriptions, preset titles); the security boundary is
#: that a planner can only select ids.
_INSTRUCTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"ignore (all |any )?(previous|prior|above) ",
    r"disregard ",
    r"system prompt",
    r"you are (now )?",
    r"new instructions",
    r"instead (output|return|add)",
    r"\bassistant\b",
    r"\bdeveloper message\b",
    r"<\/?(system|tool|instructions?)>",
)]


def looks_like_instruction(text: object) -> bool:
    """True when the text reads like an instruction to a model (the nine pinned patterns)."""
    if not isinstance(text, str):
        return False
    return any(p.search(text) for p in _INSTRUCTION_PATTERNS)


#: Span patterns removed from a request before any reuse (D3.2 step 1): identifiers the label
#: policy refuses (IPv4 and CIDR, ARN, 12-digit account id, email, resource id, hostname).
_SENSITIVE_SPANS = [
    re.compile(r"(?i)\barn:[^\s]{1,2000}"),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b"),
    re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}"),
    re.compile(r"\b\d{12}\b"),
    re.compile(r"(?i)\b(?:vpc|subnet|sg|vpce|i|eni|rtb|igw|nat)-[0-9a-f]{8,17}\b"),
    re.compile(r"(?i)\b[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63}){2,}\b"),
]


def _secret_span_patterns() -> List["re.Pattern"]:
    from ..data_protection import _SECRET_DETECTORS
    from ..governance_gate import _T6_SECRET_PATTERNS
    pats = [re.compile(p) for p, _why in _T6_SECRET_PATTERNS]
    pats += [p for p, _label, _ph in _SECRET_DETECTORS]
    return pats


def strip_sensitive(text: str) -> str:
    """Remove (never redact-in-place) every span that matches the gate's Tenet 6 secret patterns,
    the data-protection secret detectors, or the label policy's identifier patterns. The result
    is `safe_request`; the raw request is never used again (D3.2 step 1)."""
    if not isinstance(text, str):
        return ""
    out = text
    for pat in _secret_span_patterns() + _SENSITIVE_SPANS:
        out = pat.sub(" ", out)
    return out
