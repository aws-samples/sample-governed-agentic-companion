"""Staged mode — an optional intent -> plan -> approve -> execute loop over the orchestrator.

This adapts the AI-DLC operating model (and its Appendix-A prompt discipline: write a checkboxed
plan, get human approval, then execute one step at a time, marking steps done) into GAC — WITHOUT
changing GAC's nature:

  * The orchestrator stays SINGLE-HOP and deterministic. Staged mode is an opt-in WRAPPER that
    sequences several ordinary gated orchestrator calls; it does not add autonomous agent-to-agent
    spawning (EC-004 stays true).
  * "Execute a step" means PRODUCE that step's review-ready artifact, gated — it NEVER runs,
    deploys, or mutates anything (Tenet 1 is absolute). A plan is a sequence of things to draft and
    review, not things the companion does to an environment.
  * HUMAN APPROVAL is a hard gate between planning and execution: a plan is not executed until a
    human marks it approved. This mirrors AI-DLC's approval gate and GAC's human-in-the-loop stance.
  * Every executed step's output passes the always-on GovernanceGate (tagged stage=<step id>,
    front_door="staged") and therefore flows into the action audit trail and the feedback loop.

Plans are persisted as JSON under `.plans/<plan-id>.json` (gitignored) — the "context memory"
the plan accrues as it progresses, with backward/forward traceability (each step records the
intent it derives from and the artifact digest it produced).

Deterministic, stdlib-only, fail-open on persistence errors (a plan is a convenience structure;
losing it never corrupts governance). Nothing here is on by default — the engine's `ask` path is
unchanged; staged mode is reached only via the explicit `plan` command.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLANS_DIR = _REPO_ROOT / ".plans"

STATUS_DRAFT = "draft"            # plan proposed, awaiting human approval
STATUS_APPROVED = "approved"      # human approved; steps may be executed
STATUS_EXECUTING = "executing"
STATUS_DONE = "done"


def _plan_id(intent: str) -> str:
    import hashlib
    return hashlib.sha256((intent or "").strip().lower().encode()).hexdigest()[:10]


@dataclass
class PlanStep:
    id: str
    description: str
    done: bool = False
    artifact_digest: Optional[str] = None   # sha of the gated artifact this step produced
    blocked: bool = False                   # the gate blocked this step's artifact


@dataclass
class Plan:
    id: str
    intent: str
    status: str
    steps: List[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: round(time.time(), 3))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Plan":
        steps = [PlanStep(**s) for s in d.get("steps", [])]
        return Plan(id=d["id"], intent=d["intent"], status=d["status"], steps=steps,
                    created_at=d.get("created_at", 0.0))

    def render_markdown(self) -> str:
        """Checkboxed plan — the human-reviewable artifact (AI-DLC Appendix-A shape)."""
        lines = [f"# Plan: {self.intent}", "",
                 f"- id: `{self.id}`  ·  status: **{self.status}**", ""]
        for s in self.steps:
            box = "x" if s.done else " "
            mark = " ⛔ (blocked by gate)" if s.blocked else ""
            lines.append(f"- [{box}] {s.id}. {s.description}{mark}")
        lines += ["",
                  "_A human approves this plan before any step executes. Executing a step "
                  "produces a review-ready, gated artifact — it never deploys or mutates "
                  "anything (Tenet 1)._"]
        return "\n".join(lines)


class StagedPlanner:
    """Build, persist, approve, and execute staged plans over an orchestrator."""

    def __init__(self, orchestrator, plans_dir: Optional[Path] = None):
        self.orch = orchestrator
        self.dir = plans_dir or _PLANS_DIR

    # -- persistence -------------------------------------------------------------

    def _path(self, plan_id: str) -> Path:
        return self.dir / f"{plan_id}.json"

    def _save(self, plan: Plan) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(plan.id).write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:  # fail-open: a plan is a convenience, not a governance artifact
            logger.debug("plan save failed (fail-open): %s", exc)

    def load(self, plan_id: str) -> Optional[Plan]:
        try:
            p = self._path(plan_id)
            if not p.is_file():
                return None
            return Plan.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    # -- plan (deterministic decomposition) --------------------------------------

    def plan(self, intent: str) -> Plan:
        """Decompose an intent into an ordered, checkboxed plan (DRAFT, awaiting approval).

        Decomposition is deterministic and transparent: the intent is split into the natural
        'produce a review-ready artifact' steps the companion actually supports — understand the
        request, draft the artifact via the routed specialist, and self-review it against the
        gate. This is intentionally modest (GAC produces artifacts, it does not run an SDLC):
        the value is the approval gate + per-step gating + traceability, not an opinionated
        stage graph. A fork with an LLM tier can override this to produce a richer plan; the
        execution contract (approve-then-gated-steps) is unchanged."""
        pid = _plan_id(intent)
        route = self.orch.route(intent)
        steps = [
            PlanStep("1", f"Clarify and restate the intent for the '{route}' specialist, "
                          "surfacing assumptions and acceptance criteria."),
            PlanStep("2", f"Draft the review-ready artifact for the intent via the '{route}' "
                          "specialist (gated)."),
            PlanStep("3", "Self-review the drafted artifact against the governance gate and the "
                          "grounding fact bar; record gaps for the feedback loop."),
        ]
        plan = Plan(id=pid, intent=intent, status=STATUS_DRAFT, steps=steps)
        self._save(plan)
        return plan

    def approve(self, plan_id: str) -> Dict[str, Any]:
        """HUMAN action: approve a draft plan so its steps may execute. This is the hard gate
        between planning and execution — nothing executes without it."""
        plan = self.load(plan_id)
        if not plan:
            return {"ok": False, "message": f"no plan {plan_id}"}
        if plan.status not in (STATUS_DRAFT,):
            return {"ok": False, "message": f"plan {plan_id} is '{plan.status}', not a draft"}
        plan.status = STATUS_APPROVED
        self._save(plan)
        return {"ok": True, "message": f"plan {plan_id} approved — steps may now execute"}

    # -- execute (one gated step at a time) --------------------------------------

    def execute_next(self, plan_id: str, environment: str = "dev") -> Dict[str, Any]:
        """Execute the next not-done step: produce its gated artifact, mark it done, persist.

        REFUSES to execute unless the plan is human-approved (Tenet-aligned: human-in-the-loop).
        'Executing' a step routes it through the ordinary gated orchestrator — so the artifact
        passes the always-on gate, lands in the audit trail (stage=<step id>, front_door
        'staged'), and feeds the feedback loop. It never runs or deploys anything."""
        plan = self.load(plan_id)
        if not plan:
            return {"ok": False, "message": f"no plan {plan_id}"}
        if plan.status == STATUS_DRAFT:
            return {"ok": False,
                    "message": f"plan {plan_id} is not approved. Approve it first "
                               f"(./run.sh plan approve --id {plan_id}) — a human approves "
                               "before any step executes."}
        nxt = next((s for s in plan.steps if not s.done), None)
        if nxt is None:
            plan.status = STATUS_DONE
            self._save(plan)
            return {"ok": True, "message": f"plan {plan_id} complete", "artifact": "",
                    "done": True}
        plan.status = STATUS_EXECUTING
        # Produce the step artifact through the gated orchestrator. We pass the step description
        # as the request; the orchestrator routes + gates it (stage + front_door tag the audit).
        step_request = f"[intent: {plan.intent}] {nxt.description}"
        artifact = self.orch.handle(step_request, environment=environment,
                                    stage=f"plan:{plan.id}:step{nxt.id}", front_door="staged")
        import hashlib
        nxt.artifact_digest = hashlib.sha256(artifact.encode("utf-8", "replace")).hexdigest()[:16]
        nxt.blocked = artifact.lstrip().startswith("⛔")
        nxt.done = True
        if all(s.done for s in plan.steps):
            plan.status = STATUS_DONE
        self._save(plan)
        return {"ok": True, "message": f"executed step {nxt.id}", "artifact": artifact,
                "step": nxt.id, "blocked": nxt.blocked,
                "done": all(s.done for s in plan.steps)}
