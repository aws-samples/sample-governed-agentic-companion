"""Optional BIBLE-markdown evidence provider.

Parses a generated BIBLE.md claim ledger into EvidenceClaim/EvidenceReference objects and
builds an EvidencePacket for the evidence gateway (see ../evidence_gateway.py). This
module owns every BIBLE-markdown-specific assumption (the claim heading grammar, the
claim-selection heuristic, the on-disk corpus). The generic EvidenceProvider contract in
evidence_gateway.py has no knowledge of any of it.

CORPUS = A POINTER INTO THE REAL KNOWLEDGE BASE, NOT A RIVAL LEDGER (Tenet 8): the BIBLE.md
claim entries cite Evidence lines that resolve (via GAC_EVIDENCE_ROOTS) into the existing
knowledge/ tree. The corpus must not restate facts that live in the knowledge base — it
references them; the knowledge base remains the authority.

Supported grammar (see docs/architecture/evidence-gateway.md for the full schema):

    ## SECTION NAME

    ### R-<SECTION>-<N>. <assertion text>
    - Class: INVARIANT | POINT-IN-TIME | CONTESTED | SUPERSEDED | <other>
    - AsOf: <date>                     (optional)
    - Authority: <AUTHORIZED|UNAUDITED|NOT-COVERED|...>  (optional)
    - Evidence: <path>:<line-spec>     (repeatable; line-spec e.g. "10-20,45")
    - Position: <text>                 (repeatable, for CONTESTED claims)

No provider selection, discovery, or configuration in this file falls back to a home
directory or any hardcoded vault path. The BIBLE path and allowed evidence roots must be
supplied explicitly via GAC_BIBLE_PATH / GAC_EVIDENCE_ROOTS (or the constructor).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from ..evidence_gateway import (
    EvidenceClaim,
    EvidencePacket,
    EvidenceReference,
    derive_status,
    parse_line_ranges,
    redact_secrets,
    request_digest,
    resolve_within_roots,
)

_SECTION_HEADING_RE = re.compile(r"^## (.+)$")
_CLAIM_HEADING_RE = re.compile(r"^### (R-[A-Z0-9-]+-\d+)\.\s+(.*)$")
_BULLET_RE = re.compile(r"^- (Class|AsOf|Authority):\s*(.*)$")
_EVIDENCE_LINE_RE = re.compile(r"^- Evidence:\s*(.+?):(\d[\d,\-]*)\s*$")
_POSITION_RE = re.compile(r"^- Position:\s*(.*)$")

_DEFAULT_MAX_CLAIMS = 12
_MAX_EVIDENCE_PER_CLAIM = 4
_DEFAULT_CONTEXT_LINES = 12

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 4

_GENERIC_STOPWORDS = {
    "cloud", "environment", "environments", "system", "systems", "server", "servers",
}
_MIN_MATCHING_TOKENS = 2
_MIN_SINGLE_TOKEN_LENGTH = 6


def _split_root_list(roots_str: str) -> List[str]:
    """Split a GAC_EVIDENCE_ROOTS value into individual paths.

    Splits on ';' first (the only unambiguous separator, since ':' is also a Windows
    drive-letter character). A chunk that starts with a drive letter is kept whole
    rather than split again on ':'; any other chunk may still use ':' as a POSIX-style
    separator.
    """
    paths: List[str] = []
    for chunk in roots_str.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _DRIVE_LETTER_RE.match(chunk):
            paths.append(chunk)
        else:
            paths.extend(p.strip() for p in chunk.split(":") if p.strip())
    return paths


def _tokenize(text: str) -> set:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN}


class BibleMarkdownProvider:
    """Reads one BIBLE.md file and builds packets scoped to a request."""

    provider_id = "bible-markdown"
    provider_version = "1.0.0"

    def __init__(
        self,
        bible_path: Path,
        allowed_roots: Sequence[Path],
        max_claims: int = _DEFAULT_MAX_CLAIMS,
    ):
        self.bible_path = Path(bible_path)
        self.allowed_roots = [Path(r) for r in allowed_roots]
        self.max_claims = max_claims
        text = self.bible_path.read_text(encoding="utf-8")
        self.corpus_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._claims = self._parse(text)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        path_env_key: str = "GAC_BIBLE_PATH",
        roots_env_key: str = "GAC_EVIDENCE_ROOTS",
        max_claims: int = _DEFAULT_MAX_CLAIMS,
    ) -> "BibleMarkdownProvider":
        """Construct from the env vars named by path_env_key/roots_env_key (config.yaml's
        evidence.path_env / evidence.allowed_roots_env). No fallback path.
        """
        path_str = env.get(path_env_key)
        if not path_str:
            raise FileNotFoundError(f"{path_env_key} is not set")
        bible_path = Path(path_str)
        if not bible_path.is_file():
            raise FileNotFoundError(f"{path_env_key} does not point to a file: {bible_path}")

        roots_str = env.get(roots_env_key)
        if roots_str:
            roots = [Path(p) for p in _split_root_list(roots_str)]
        else:
            roots = [bible_path.parent]
        return cls(bible_path, roots, max_claims=max_claims)

    # -- parsing -------------------------------------------------------------------

    def _parse(self, text: str) -> List[dict]:
        claims: List[dict] = []
        current: Optional[dict] = None
        section = ""
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            heading = _SECTION_HEADING_RE.match(line)
            if heading and not line.startswith("###"):
                section = heading.group(1).strip()
                continue
            claim_heading = _CLAIM_HEADING_RE.match(line)
            if claim_heading:
                if current is not None:
                    claims.append(current)
                current = {
                    "claim_id": claim_heading.group(1),
                    "section": section,
                    "assertion": claim_heading.group(2).strip(),
                    "claim_class": "UNSPECIFIED",
                    "as_of": "",
                    "authority": "UNSPECIFIED",
                    "evidence": [],
                    "positions": [],
                }
                continue
            if current is None:
                continue
            stripped = line.strip()
            bullet = _BULLET_RE.match(stripped)
            if bullet:
                field_name, value = bullet.group(1), bullet.group(2).strip()
                if field_name == "Class":
                    current["claim_class"] = value.upper()
                elif field_name == "AsOf":
                    current["as_of"] = value
                elif field_name == "Authority":
                    current["authority"] = value.upper()
                continue
            evidence_line = _EVIDENCE_LINE_RE.match(stripped)
            if evidence_line:
                current["evidence"].append((evidence_line.group(1).strip(), evidence_line.group(2).strip()))
                continue
            position = _POSITION_RE.match(stripped)
            if position:
                current["positions"].append(position.group(1).strip())
        if current is not None:
            claims.append(current)
        return claims

    # -- selection -------------------------------------------------------------------

    def _select(self, request: str) -> List[dict]:
        """Select claims for this request.

        An explicit claim ID in the request selects exactly that claim (word-boundary
        match, so "R-A-1" cannot match "R-A-10"). If the request names any claim IDs,
        those are the whole selection: natural-language matching is not layered on top,
        so a query pinned to specific claims cannot be diluted by unrelated ones.

        With no explicit ID, a claim is selected only when its assertion shares enough
        specific vocabulary with the request (see _MIN_MATCHING_TOKENS /
        _MIN_SINGLE_TOKEN_LENGTH) after generic domain words are excluded, so a shared
        word like "cloud" cannot select a claim on its own.
        """
        explicit = [c for c in self._claims if self._claim_id_in_request(c["claim_id"], request)]
        if explicit:
            return sorted(explicit, key=lambda c: c["claim_id"])

        request_tokens = _tokenize(request)
        scored = []
        for claim in self._claims:
            matching = (request_tokens & _tokenize(claim["assertion"])) - _GENERIC_STOPWORDS
            if len(matching) >= _MIN_MATCHING_TOKENS or any(
                len(t) >= _MIN_SINGLE_TOKEN_LENGTH for t in matching
            ):
                scored.append((len(matching), claim["claim_id"], claim))
        scored.sort(key=lambda tup: (-tup[0], tup[1]))
        return [c for _, _, c in scored[: self.max_claims]]

    @staticmethod
    def _claim_id_in_request(claim_id: str, request: str) -> bool:
        return re.search(r"\b" + re.escape(claim_id) + r"\b", request) is not None

    # -- evidence reopening -------------------------------------------------------------

    def _reopen(self, path_str: str, lines_spec: str) -> EvidenceReference:
        citation = f"{path_str}:{lines_spec}"
        resolved = resolve_within_roots(path_str, self.allowed_roots)
        if resolved is None:
            return EvidenceReference(
                citation=citation, path=path_str,
                error="evidence path not permitted by allowed roots, or not found",
            )
        line_ranges = parse_line_ranges(lines_spec)
        try:
            all_lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return EvidenceReference(citation=citation, path=str(resolved), error=str(exc))

        effective_ranges = line_ranges or ((1, min(len(all_lines), _DEFAULT_CONTEXT_LINES)),)
        excerpt_parts = [
            "\n".join(all_lines[max(start - 1, 0):end]) for start, end in effective_ranges
        ]
        raw_excerpt = "\n\n".join(excerpt_parts)
        digest = hashlib.sha256(raw_excerpt.encode("utf-8")).hexdigest()
        return EvidenceReference(
            citation=citation,
            path=str(resolved),
            line_ranges=effective_ranges,
            reopened=True,
            excerpt=redact_secrets(raw_excerpt),
            digest=digest,
        )

    # -- packet building -------------------------------------------------------------

    def build_packet(self, request: str, action_mode: str, route: Sequence[str]) -> EvidencePacket:
        selected = self._select(request)
        gaps: List[str] = []
        claims: List[EvidenceClaim] = []

        for c in selected:
            refs = [self._reopen(p, spec) for p, spec in c["evidence"][:_MAX_EVIDENCE_PER_CLAIM]]
            if not any(r.reopened for r in refs):
                reason = "cites no evidence" if not c["evidence"] else "no evidence citation could be reopened"
                gaps.append(f"{c['claim_id']}: {reason}")
            claims.append(EvidenceClaim(
                claim_id=c["claim_id"],
                claim_class=c["claim_class"],
                assertion=c["assertion"],
                as_of=c["as_of"],
                authority=c["authority"],
                references=tuple(refs),
            ))

        if not selected:
            gaps.append("no claims in the corpus matched this request")

        current_state_required = any(
            kw in request.lower() for kw in (
                "current", "currently", "now", "right now", "today", "live", "status",
                "present", "presently", "up to date", "up-to-date", "still",
            )
        )
        status, stop_conditions, live_checks = derive_status(claims, gaps, current_state_required)

        return EvidencePacket(
            request_digest=request_digest(request),
            action_mode=action_mode,
            route=tuple(route),
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            corpus_digest=self.corpus_digest,
            status=status,
            claims=tuple(claims),
            gaps=tuple(gaps),
            stop_conditions=stop_conditions,
            live_checks=live_checks,
        )
