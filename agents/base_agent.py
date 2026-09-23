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
        return (score, sorted(matched))

    def assess_confidence(self, response: str, query: str = "") -> float:
        return self.assess_confidence_detail(response, query)[0]

    # -- the contract ------------------------------------------------------------

    def handle_request(self, request: str, environment: str = "dev") -> str:
        """Answer a request. Subclasses override; the base gives a grounded lookup."""
        answer = self._answer_from_knowledge(request)
        if answer:
            return answer
        return (
            f"[{self.name}] I can't ground an answer to this in my current knowledge base "
            "yet, so I won't guess (Tenet 4). Let's move it forward together rather than stop "
            "here:\n"
            "  1. If another specialist owns it, re-route via the Orchestrator: "
            f'./run.sh ask "{request.strip()}"\n'
            "  2. If it IS in my area but I'm missing specifics, tell me the concrete artifact "
            "or constraint and I'll ground a draft on it.\n"
            "  3. If it's a new requirement beyond the knowledge base, describe the outcome you "
            "want and we'll shape the approach together (a human still builds and deploys, "
            "Tenet 1); capture the facts we confirm so the knowledge base grows for the next "
            "builder."
        )

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
