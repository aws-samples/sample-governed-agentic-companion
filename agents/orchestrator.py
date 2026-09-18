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
from typing import Dict, List, Tuple

from .governance_gate import gate_output
from .specialists import build_specialists

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self):
        # name -> (agent, routing keywords)
        self._specialists: Dict[str, Tuple[object, List[str]]] = build_specialists()

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
        """Route, delegate, and return the GATED response. This is the one entry point."""
        name = self.route(request)
        agent, _kw = self._specialists[name]
        logger.info("[Orchestrator] routing to %s", name)
        raw = agent.handle_request(request, environment)
        # EVERY response leaves through the gate — confidence/sources measured from the agent.
        return gate_output(raw, agent=name, request=request, environment=environment,
                           agent_obj=agent)
