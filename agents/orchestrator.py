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

from .config import load_config
from .evidence_gateway import (
    EvidenceProvider,
    UnavailableProvider,
    build_evidence_packet,
    packet_validity_reason,
)
from .governance_gate import gate_output, gate_output_result
from .specialists import build_specialists

logger = logging.getLogger(__name__)

#: The evidence pre-dispatch block text (unchanged wording; formatted with name and reason).
EVIDENCE_BLOCK = (
    "⛔ Response blocked by the evidence gateway ({name}).\n\n"
    "Evidence packet check failed ({reason}); dependent work must stop "
    "until it is resolved. This is a review-ready block, not an executed action."
)

#: The diagram path truncates the evidence basis to this many bytes at a line boundary.
_EVIDENCE_BASIS_CAP = 1024


def _cap_lines(text: str, cap: int) -> str:
    if len(text.encode("utf-8")) <= cap:
        return text
    out, used = [], 0
    for line in text.split("\n"):
        n = len(line.encode("utf-8")) + 1
        if used + n > cap:
            break
        out.append(line)
        used += n
    return "\n".join(out)


# Kept as an alias so existing imports of orchestrator._load_config keep working.
_load_config = load_config


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
        #: compact diagram_manifest of the last passing diagram render (front doors read it)
        self.last_manifest: Optional[dict] = None
        #: True when the last diagram request passed the gate without producing a file
        self.last_declined: bool = False

    def _keyword_score(self, name: str, req: str) -> int:
        return sum(1 for kw in self._specialists[name][1] if kw in req)

    def route(self, request: str) -> str:
        """Pick the specialist whose keywords best match the request (deterministic).

        A specialist exposing matches_intent(request, other_score) is asked first, in registry
        order (the diagram specialist's narrow draw-intent rules); other_score(text) is the best
        keyword score any OTHER specialist gets for text. Otherwise keyword scoring runs as
        before."""
        for name, (agent, _kw) in self._specialists.items():
            fn = getattr(agent, "matches_intent", None)
            if callable(fn):
                def other(text: str, _me: str = name) -> int:
                    t = text.lower()
                    return max([self._keyword_score(n, t) for n in self._specialists if n != _me]
                               or [0])
                if fn(request, other):
                    return name
        req = request.lower()
        best_name, best_score = None, 0
        for name in self._specialists:
            score = self._keyword_score(name, req)
            if score > best_score:
                best_name, best_score = name, score
        # Default to the first specialist if nothing matched (still gated).
        return best_name or next(iter(self._specialists))

    def handle_with_artifacts(self, request: str, environment: str = "dev", *,
                              force: Optional[str] = None, deliver: bool = True,
                              title: str = "", name: str = "",
                              stage: Optional[str] = None,
                              front_door: str = "engine",
                              planner: str = "") -> Tuple[str, Optional[dict]]:
        """Route (or select `force` when it names a registered specialist), run the evidence
        pre-dispatch check exactly as handle() does, dispatch, and gate. Returns (gated text,
        artefact bundle or None). A bundle is returned only when `deliver` is true and the gate
        passed; forcing a specialist bypasses neither the evidence check nor the gate. For a
        multi-view diagram the bundle is view 1; handle_with_views returns every view."""
        text, bundles = self.handle_with_views(request, environment, force=force,
                                               deliver=deliver, title=title, name=name,
                                               stage=stage, front_door=front_door,
                                               planner=planner)
        return text, (bundles[0] if bundles else None)

    def handle_with_views(self, request: str, environment: str = "dev", *,
                          force: Optional[str] = None, deliver: bool = True,
                          title: str = "", name: str = "",
                          stage: Optional[str] = None,
                          front_door: str = "engine",
                          planner: str = "") -> Tuple[str, List[dict]]:
        """As handle_with_artifacts, but returns (gated text, [bundle per delivered view]).
        `planner` ("" or "deterministic") can only downgrade the diagram planner. After the
        call, `last_declined` is True when a diagram request passed the gate without a file
        (an honest decline)."""
        rname = force if force in self._specialists else self.route(request)
        agent, _kw = self._specialists[rname]
        logger.info("[Orchestrator] routing to %s", rname)
        self.last_declined = False

        evidence_packet = build_evidence_packet(
            self.evidence_provider, request, "assessment", [rname]
        )
        if evidence_packet is not None:
            reason = packet_validity_reason(evidence_packet, request, rname)
            if reason:
                block = EVIDENCE_BLOCK.format(name=rname, reason=reason)
                # Still pass through the gate for a consistent, audited outcome.
                return gate_output(block, agent=rname, request=request,
                                   environment=environment, agent_obj=agent,
                                   used_llm=False, confidence=1.0, footer=False,
                                   stage=stage, front_door=front_door), []

        render = getattr(agent, "render_request", None)
        if callable(render):
            rr = render(request, environment, deliver=deliver, title=title, name=name,
                        wire_budget=front_door != "cli", planner=planner)
            raw = rr.raw_text
            if evidence_packet is not None:
                raw += "\n" + _cap_lines(evidence_packet.render_basis(), _EVIDENCE_BASIS_CAP)
            # Verified zone with explicit sources: no recomputed heuristic score (agent_obj=None).
            # An LLM-planned render (opt-in) is exploration zone with its measured traceability.
            result = gate_output_result(raw, agent=rname, request=request,
                                        environment=environment, agent_obj=None,
                                        used_llm=bool(getattr(rr, "used_llm", False)),
                                        confidence=getattr(rr, "confidence", None),
                                        sources=rr.sources,
                                        evidence=evidence_packet, stage=stage,
                                        front_door=front_door,
                                        footer_lines=list(getattr(rr, "footer_lines", []) or []),
                                        audit_extra=dict(getattr(rr, "audit_extra", {}) or {}))
            self.last_manifest = None if result.blocked else rr.manifest
            bundles = list(getattr(rr, "bundles", []) or []) or \
                ([rr.bundle] if rr.bundle is not None else [])
            self.last_declined = (not result.blocked) and not bundles
            return result.response, (bundles if (deliver and not result.blocked) else [])

        raw = agent.handle_request(request, environment)
        if evidence_packet is not None:
            raw += "\n" + evidence_packet.render_basis()
        # EVERY response leaves through the gate — confidence/sources measured from the agent.
        return gate_output(raw, agent=rname, request=request, environment=environment,
                           agent_obj=agent, evidence=evidence_packet,
                           stage=stage, front_door=front_door), []

    def handle(self, request: str, environment: str = "dev", *,
               stage: Optional[str] = None, front_door: str = "engine") -> str:
        """Route, delegate, and return the GATED response. This is the one entry point.

        When an evidence provider is configured, a packet is built for the route BEFORE
        dispatch; a packet that does not authorize this specialist (or is not READY) blocks the
        response instead of calling handle_request. No-op when the provider is None (the
        default): behavior is unchanged.

        `stage` and `front_door` are audit-only labels (default None/"engine"), passed straight
        to the gate so a staged-mode step or a non-engine caller is attributable in the audit
        trail. They never affect routing or enforcement."""
        return self.handle_with_artifacts(request, environment, deliver=False,
                                          stage=stage, front_door=front_door)[0]
