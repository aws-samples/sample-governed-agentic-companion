"""BaseAgent — the common base every specialist extends.

Provides the three things every governed specialist needs:
  1. Knowledge loading (agent-scoped — only the files relevant to the agent's domain).
  2. MEASURED confidence + grounding sources (never guessed) — the input the gate uses to
     enforce grounding (Tenet 4) and to render the honest footer.
  3. Confidence-aware formatting.

The public contract is uniform: handle_request(request, environment) -> str, so the
Orchestrator and every front door can call any agent the same way.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

import yaml

logger = logging.getLogger(__name__)

_KNOWLEDGE_ROOT = Path(__file__).resolve().parent.parent / "knowledge"


class BaseAgent:
    #: subclasses set the knowledge subdirectories (under knowledge/) they ground against.
    knowledge_dirs: List[str] = []
    #: whether an LLM is available to this agent (drives the trust zone). Default deterministic.
    llm_available: bool = False

    def __init__(self, name: str):
        self.name = name
        self._knowledge: dict = {}
        self.load_knowledge()

    # -- knowledge ---------------------------------------------------------------

    def load_knowledge(self) -> None:
        """Load this agent's slice of the KB (Tier-A baseline; Tier-B overlay merges on top)."""
        self._knowledge = {}
        for sub in self.knowledge_dirs:
            base = _KNOWLEDGE_ROOT / sub
            if not base.exists():
                continue
            for path in base.rglob("*.yaml"):
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    key = str(path.relative_to(_KNOWLEDGE_ROOT)).replace(".yaml", "")
                    self._knowledge[key] = data
                except Exception as e:  # a bad file must not crash the agent
                    logger.warning("skip knowledge %s: %s", path, e)
        # Optional Tier-B S3 overlay (opt-in; fails open to the baseline just loaded).
        try:
            from .kb_overlay import apply_overlay
            self._knowledge = apply_overlay(self._knowledge)
        except Exception:
            pass

    def _knowledge_text(self) -> List[Tuple[str, str]]:
        """(key, flattened-text) for each loaded knowledge file. The key/filename is included in
        the searchable text — a filename like `.../deployment_policy` is legitimate grounding
        metadata (it names what the file is about)."""
        return [(key, (key.replace("/", " ").replace("_", " ") + " "
                       + yaml.safe_dump(data, default_flow_style=False)).lower())
                for key, data in self._knowledge.items()]

    # -- measured confidence + sources -------------------------------------------

    #: content words that don't help disambiguate a match (kept out of scoring)
    _STOPWORDS = {"what", "which", "where", "when", "does", "with", "from", "into", "this",
                  "that", "the", "and", "for", "are", "how", "why", "your", "you"}

    def assess_confidence_detail(self, response: str, query: str) -> Tuple[float, List[str]]:
        """Return (score, sources) — MEASURED, never guessed. Sources are the knowledge keys
        whose content overlaps the response/query. The gate uses this to enforce grounding.

        The match threshold is proportional to the number of MEANINGFUL query/response terms:
        a short question can ground on a couple of strong term hits, while a long response needs
        proportionally more — so grounding stays honest for both the answer-lookup path (short
        query) and the gate path (longer response)."""
        terms = {t for t in re.findall(r"[a-z0-9]{4,}", (response + " " + query).lower())
                 if t not in self._STOPWORDS}
        if not terms:
            return (0.5, [])
        # Require at least 2 hits, or ~40% of the meaningful terms, whichever is larger.
        threshold = max(2, round(len(terms) * 0.4))
        matched = []
        best_coverage = 0.0
        for key, text in self._knowledge_text():
            hits = sum(1 for t in terms if t in text)
            if hits >= threshold:
                matched.append(key)
                best_coverage = max(best_coverage, hits / len(terms))
        if not matched:
            return (0.5, [])  # nothing grounded it -> below the fact bar
        base = 0.6 if not self.llm_available else 0.7
        span = 0.4 if not self.llm_available else 0.3
        score = min(1.0, base + best_coverage * span)
        # RELEVANCE DAMPING (Tenet 4 honesty): coverage measures "uses our KB vocabulary",
        # NOT "answers what was asked". A response full of domain words that ignores the
        # question could otherwise score high on vocabulary alone. Damp the score by how many
        # of the REQUEST's distinctive terms the RESPONSE actually addresses. An on-topic
        # answer is unpenalised; an off-topic one cannot reach the fact bar on vocabulary.
        score *= self._request_relevance(query, response)
        return (round(score, 4), sorted(matched))

    def assess_confidence(self, response: str, query: str = "") -> float:
        return self.assess_confidence_detail(response, query)[0]

    #: fraction of a request's distinctive terms an answer must address to count fully on-topic
    #: (relevance multiplier 1.0); below 1.0 so paraphrase isn't falsely pushed under the bar.
    _HIGH_RELEVANCE_FRACTION = 0.8
    #: shared leading-char length treated as a stem match (so "tune"~"tuning", "consumer"~"consumers")
    _RELEVANCE_STEM_LEN = 5

    def _request_relevance(self, query: str, response: str) -> float:
        """Fraction of the request's DISTINCTIVE terms the response addresses, mapped to a
        relevance multiplier in [0.4, 1.0].

        1.0 when the request has no distinctive terms or the response engages (at/above
        _HIGH_RELEVANCE_FRACTION of) them; falls linearly to a 0.4 floor as the response
        ignores more of what was asked. A term is matched by exact substring OR a shared word
        stem, so paraphrase ("tuning" for "tune") is not falsely penalised. Read-only; never
        raises. Off-topic answers still land near the floor and stay below the fact bar."""
        try:
            q_terms = {t for t in re.findall(r"[a-z0-9]{4,}", (query or "").lower())
                       if t not in self._STOPWORDS}
            if not q_terms:
                return 1.0
            resp = (response or "").lower()
            resp_words = set(re.findall(r"[a-z0-9]{4,}", resp))
            stem = self._RELEVANCE_STEM_LEN

            def _addressed(term: str) -> bool:
                if term in resp:
                    return True
                t_stem = term[:stem]
                if len(t_stem) < 4:
                    return False
                return any(w.startswith(t_stem) or term.startswith(w[:stem])
                           for w in resp_words if len(w) >= 4)

            fraction = sum(1 for t in q_terms if _addressed(t)) / len(q_terms)
            if fraction >= self._HIGH_RELEVANCE_FRACTION:
                return 1.0
            return 0.4 + (1.0 - 0.4) * (fraction / self._HIGH_RELEVANCE_FRACTION)
        except Exception:
            return 1.0

    # -- the contract ------------------------------------------------------------

    def handle_request(self, request: str, environment: str = "dev") -> str:
        """Answer a request. Subclasses override; the base gives a grounded lookup."""
        answer = self._answer_from_knowledge(request)
        if answer:
            return answer
        return self._peering_decline(request)

    def _peering_decline(self, request: str, *, scope_hint: str = "") -> str:
        """A PEERING decline: honest that the answer isn't grounded in the current knowledge
        base, but a companion move — offer paths forward instead of dead-ending. Governance is
        intact (no fabrication, no action, describe-only).

        This is the graceful alternative to a hard governance-gate block. A fork that adds an
        LLM tier should route an ungrounded LLM answer through here (see llm_answer_or_decline)
        rather than let the gate block it with a bare wall — the user gets a scope-boundary
        redirect, not a dead end. We say "not grounded yet" (true), never "out of scope"
        (which we can't know)."""
        scope = scope_hint or f"the {self.name} area"
        return (
            f"[{self.name}] I can't ground an answer to this in my current knowledge base "
            "yet, so I won't guess (Tenet 4). Let's move it forward together rather than stop "
            "here:\n"
            "  1. If another specialist owns it, re-route via the Orchestrator: "
            f'./run.sh ask "{request.strip()}"\n'
            f"  2. If it IS in {scope} but I'm missing specifics, tell me the concrete artifact "
            "or constraint and I'll ground a draft on it.\n"
            "  3. If it's a new requirement beyond the knowledge base, describe the outcome you "
            "want and we'll shape the approach together (a human still builds and deploys, "
            "Tenet 1); capture the facts we confirm so the knowledge base grows for the next "
            "builder."
        )

    def llm_answer_or_decline(self, request: str, environment: str, *,
                              llm_answer: str, scope_hint: str = "") -> str:
        """Parity helper for FORKS that add an LLM tier (the example kit is deterministic-only,
        so nothing here calls it yet — it is ready infrastructure, not a live path).

        Given an already-produced `llm_answer`, deliver it only if it clears the grounding
        fact bar; otherwise degrade to a PEERING decline instead of letting the governance gate
        hard-block the ungrounded answer. This keeps a fork's LLM path from dead-ending the user
        with a bare "⛔ blocked" wall — the agent declines honestly and routes. Mirrors the same
        contract used in the customer-specific accelerator so forks inherit the safe behavior.

        Deterministic (Tenet-1 path) note: this takes the LLM text as an argument rather than
        calling any provider, so the base kit keeps no LLM dependency (see AGENTS.md)."""
        score = self.assess_confidence(llm_answer, request)
        if score >= self._FACT_BAR:
            return llm_answer
        return self._peering_decline(request, scope_hint=scope_hint)

    #: grounding bar an LLM (exploration-zone) answer must clear (Tenet 4). Sourced from the
    #: gate's own threshold so a fork's answer-or-decline decision matches what the gate enforces.
    try:
        from .governance_gate import CONFIDENCE_FACT_THRESHOLD as _FACT_BAR
    except Exception:  # pragma: no cover - defensive; gate import should always succeed
        _FACT_BAR = 0.95

    def _answer_from_knowledge(self, request: str) -> str:
        """Deterministic Tier-1 answer: surface the most relevant knowledge entry."""
        _score, sources = self.assess_confidence_detail(request, request)
        if not sources:
            return ""
        key = sources[0]
        data = self._knowledge.get(key, {})
        summary = data.get("summary") or data.get("policy") or data.get("description")
        if summary:
            return f"[{self.name}] {summary}\n(Source: {key})"
        return f"[{self.name}] Relevant knowledge: {key}\n{yaml.safe_dump(data).strip()}"
