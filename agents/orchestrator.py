"""Orchestrator — classifies each request and routes it to exactly one specialist.

The Orchestrator is deliberately thin: it never answers a domain question itself. Routing is
deterministic (keyword/intent match against each specialist's declared domain), visible (the
routing decision is stated), and single-hop. This is a SINGLE-ORCHESTRATOR topology by
deliberate choice — no autonomous agent-to-agent spawning (that is the swarm-breakout failure
mode). Every specialist invocation traces to one human-initiated request and is individually
gated on the way out.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .evidence_gateway import (
    EvidenceProvider,
    UnavailableProvider,
    build_evidence_packet,
    packet_validity_reason,
)
from .governance_gate import gate_output
from .specialists import build_specialists

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    try:
        import yaml
        for name in ("config.yaml", "config.yaml.example"):
            cfg = Path(__file__).resolve().parent.parent / name
            if cfg.exists():
                return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _get_evidence_provider(config: dict) -> Optional[EvidenceProvider]:
    """Select the evidence provider from config's `evidence:` section (see
    docs/FRONT-DOORS.md / frontdoor). Returns None when unconfigured (a no-op that preserves
    all existing behavior). A configured provider that cannot initialize fails CLOSED via
    UnavailableProvider, never open. The kit default is `evidence.provider: none` — opt-in."""
    evidence_cfg = (config or {}).get("evidence") or {}
    provider_name = evidence_cfg.get("provider", "none")
    if provider_name in (None, "none", ""):
        return None
    if provider_name == "bible_markdown":
        from .evidence_providers.bible_markdown import BibleMarkdownProvider
        path_key = evidence_cfg.get("path_env", "GAC_BIBLE_PATH")
        roots_key = evidence_cfg.get("allowed_roots_env", "GAC_EVIDENCE_ROOTS")
        max_claims = evidence_cfg.get("max_claims", 12)
        # 1) explicit env vars win (an engagement can point at a curated corpus).
        if os.environ.get(path_key):
            try:
                return BibleMarkdownProvider.from_env(
                    os.environ, path_env_key=path_key, roots_env_key=roots_key,
                    max_claims=max_claims,
                )
            except Exception as exc:
                return UnavailableProvider(provider_name, str(exc))
        # 2) repo-derived corpus (.evidence/BIBLE.md) with the knowledge/ tree as the allowed
        # evidence root. This is a POINTER into the real KB (Tenet 8), not a rival ledger. If it
        # has not been generated yet, FAIL CLOSED with a clear instruction — never silently
        # disable enforcement.
        repo_root = Path(__file__).resolve().parent.parent
        default_bible = repo_root / ".evidence" / "BIBLE.md"
        if not default_bible.is_file():
            return UnavailableProvider(
                provider_name,
                f"evidence corpus not found at {default_bible}. Generate it with "
                "`./run.sh bible` (derives it from knowledge/), or set the "
                f"{path_key}/{roots_key} env vars to a curated corpus.",
            )
        try:
            # Allowed roots: the repo root (so repo-relative citations like
            # `knowledge/tier_a/...` emitted by scripts/generate_bible.py resolve — the corpus
            # carries no absolute home-dir paths), the knowledge tree, and the corpus dir.
            # resolve_within_roots still requires each cited file to exist under a root.
            return BibleMarkdownProvider(
                default_bible,
                allowed_roots=[repo_root, repo_root / "knowledge", default_bible.parent],
                max_claims=max_claims,
            )
        except Exception as exc:
            return UnavailableProvider(provider_name, str(exc))
    return UnavailableProvider(provider_name, "unknown evidence provider name")


class Orchestrator:
    def __init__(self):
        # name -> (agent, routing keywords)
        self._specialists: Dict[str, Tuple[object, List[str]]] = build_specialists()
        # Evidence gateway: optional, config-selected (evidence.provider in config.yaml).
        # None preserves exact existing behavior; see evidence_gateway.py.
        self.evidence_provider = _get_evidence_provider(_load_config())

    def route(self, request: str) -> str:
        """Pick the specialist whose keywords best match the request (deterministic)."""
        req = request.lower()
        best_name, best_score = None, 0
        for name, (_agent, keywords) in self._specialists.items():
            score = sum(1 for kw in keywords if kw in req)
            if score > best_score:
                best_name, best_score = name, score
        # Default to the first specialist if nothing matched (still gated).
        return best_name or next(iter(self._specialists))

    def handle(self, request: str, environment: str = "dev") -> str:
        """Route, delegate, and return the GATED response. This is the one entry point.

        When an evidence provider is configured, a packet is built for the route BEFORE
        dispatch; a packet that does not authorize this specialist (or is not READY) blocks the
        response instead of calling handle_request. No-op when the provider is None (the
        default): behavior is unchanged."""
        name = self.route(request)
        agent, _kw = self._specialists[name]
        logger.info("[Orchestrator] routing to %s", name)

        evidence_packet = build_evidence_packet(
            self.evidence_provider, request, "assessment", [name]
        )
        if evidence_packet is not None:
            reason = packet_validity_reason(evidence_packet, request, name)
            if reason:
                block = (
                    f"⛔ Response blocked by the evidence gateway ({name}).\n\n"
                    f"Evidence packet check failed ({reason}); dependent work must stop "
                    "until it is resolved. This is a review-ready block, not an executed action."
                )
                # Still pass through the gate for a consistent, audited outcome.
                return gate_output(block, agent=name, request=request,
                                   environment=environment, agent_obj=agent,
                                   used_llm=False, confidence=1.0, footer=False)

        raw = agent.handle_request(request, environment)
        if evidence_packet is not None:
            raw += "\n" + evidence_packet.render_basis()
        # EVERY response leaves through the gate — confidence/sources measured from the agent.
        return gate_output(raw, agent=name, request=request, environment=environment,
                           agent_obj=agent, evidence=evidence_packet)
