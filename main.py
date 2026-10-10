#!/usr/bin/env python3
"""Governed Agentic Companion — CLI front door.

Commands:
  status                 governance integrity check + knowledge load summary
  ask "<question>"       route a question through the governed orchestrator (gated)
  gate [--file F]        run the governance gate over stdin/a file (front-door backstop)
  bible [--out F]        derive the evidence corpus (.evidence/BIBLE.md) from knowledge/

Deterministic by default — no AWS, no LLM required. Enable model tiers in config.yaml.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cmd_status(_args) -> int:
    from governance.integrity_check import GovernanceIntegrity
    from agents.specialists import build_specialists

    result = GovernanceIntegrity().verify_principles_integrity()
    icon = "✅" if result["valid"] else "⛔"
    print(f"Governed Agentic Companion — Status")
    print(f"  {icon} Governance: {result['message']}")

    specialists = build_specialists()
    print(f"  🧭 Orchestrator: {len(specialists)} specialists — {', '.join(specialists)}")
    total = 0
    for name, (agent, _kw) in specialists.items():
        n = len(getattr(agent, "_knowledge", {}))
        total += n
        print(f"     • {name}: {n} knowledge file(s)")
    print(f"  📚 Knowledge files loaded: {total}")
    return 0 if result["valid"] else 2


def cmd_ask(args) -> int:
    from agents.orchestrator import Orchestrator
    print(Orchestrator().handle(args.question, environment=args.environment))
    return 0


def cmd_gate(args) -> int:
    import os
    from agents.governance_gate import get_gate
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    # Front-door label (audit-only): a hook/MCP caller can attribute the event via
    # GAC_GATE_FRONT_DOOR (e.g. "aidlc-hook", "mcp-gate"). Default "front-door". Never affects
    # enforcement — the gate blocks the same regardless of who called it.
    front_door = os.getenv("GAC_GATE_FRONT_DOOR", "front-door")
    result = get_gate().enforce(text, agent="front-door", request="", footer=args.footer,
                                front_door=front_door)
    print(result.response)
    return 2 if result.blocked else 0


def cmd_audit(args) -> int:
    """Show the tail of the action-level audit trail (OB-001/HI-003 observability record).
    Read-only; the trail is append-only JSONL at .audit/trail.jsonl (gitignored)."""
    from agents.audit import get_audit
    events = get_audit().tail(args.lines)
    if not events:
        print("No audit events recorded yet. The trail populates as the gate runs "
              "(.audit/trail.jsonl).")
        return 0
    for e in events:
        ts = e.get("ts", "")
        ev = e.get("event", "")
        if ev == "gate_decision":
            status = "BLOCKED" if e.get("blocked") else "pass"
            fds = ",".join(f.get("code", "") for f in e.get("findings", [])) or "-"
            print(f"  {ts}  {e.get('front_door','?')}/{e.get('agent','?')}  "
                  f"zone={e.get('trust_zone','?')} conf={e.get('confidence')} "
                  f"gate={status} findings={fds}")
        else:
            print(f"  {ts}  {ev}  {{{', '.join(f'{k}={v}' for k, v in e.items() if k not in ('ts','event','schema'))}}}")
    return 0


def cmd_feedback(args) -> int:
    """Review and promote staged feedback candidates (Tenet 8: staged -> human-reviewed ->
    promoted, never silently absorbed). Promotion emits a Tier-B overlay-shaped candidate a
    human still applies; Tier-A stays locked."""
    from agents.feedback import get_feedback, STATUS_CANDIDATE, STATUS_PROMOTED
    fb = get_feedback()
    if args.action == "list":
        status = STATUS_PROMOTED if args.promoted else STATUS_CANDIDATE
        rows = fb.list_candidates(status=status)
        if not rows:
            print(f"No {status} feedback. Candidates stage automatically when a request can't "
                  "be grounded (.feedback/candidates.jsonl).")
            return 0
        print(f"{status.capitalize()} feedback ({len(rows)}):")
        for r in rows:
            print(f"  [{r['id']}] agent={r['agent']} seen={r.get('seen_count',1)}x — "
                  f"{r.get('gap','')}\n      request: {r.get('request','')[:120]}")
            if r.get("promotion"):
                print(f"      -> promoted to {r['promotion']['knowledge_key']}")
        return 0
    if args.action == "promote":
        res = fb.promote(args.id, knowledge_key=args.key, summary=args.summary,
                         layer=args.layer)
        print(("✅ " if res["ok"] else "⛔ ") + res["message"])
        if res["ok"]:
            print("   Apply the overlay entry via the Tier-B promotion (a human owns the KB "
                  "change). Tier-A stays locked.")
        return 0 if res["ok"] else 2
    if args.action == "dismiss":
        res = fb.dismiss(args.id, reason=args.reason or "")
        print(("✅ " if res["ok"] else "⛔ ") + res["message"])
        return 0 if res["ok"] else 2
    return 0


def cmd_plan(args) -> int:
    """Staged mode (opt-in): intent -> plan (checkboxed) -> human approval -> execute one gated
    step at a time. Executing a step PRODUCES a review-ready, gated artifact — it never deploys
    or mutates anything (Tenet 1). Plans persist under .plans/ (gitignored)."""
    from agents.orchestrator import Orchestrator
    from agents.staged import StagedPlanner
    planner = StagedPlanner(Orchestrator())

    if args.action == "create":
        plan = planner.plan(args.intent)
        print(plan.render_markdown())
        print(f"\nApprove to execute: ./run.sh plan approve --id {plan.id}")
        return 0
    if args.action == "show":
        plan = planner.load(args.id)
        if not plan:
            print(f"No plan {args.id}.")
            return 2
        print(plan.render_markdown())
        return 0
    if args.action == "approve":
        res = planner.approve(args.id)
        print(("✅ " if res["ok"] else "⛔ ") + res["message"])
        return 0 if res["ok"] else 2
    if args.action == "step":
        res = planner.execute_next(args.id, environment=args.environment)
        if not res["ok"]:
            print("⛔ " + res["message"])
            return 2
        if res.get("done") and not res.get("artifact"):
            print("✅ " + res["message"])
            return 0
        print(f"── step {res.get('step','?')} ──")
        print(res["artifact"])
        print(f"\n{res['message']}" + (" (plan complete)" if res.get("done") else ""))
        return 0
    return 0


def cmd_verify(args) -> int:
    """Advisory sensors + the front-door parity/determinism guard.
      --list    show the inspectable sensor manifest set
      --parity  run the front-door parity/determinism guard (AI-DLC package.ts --check analogue)
      (default) run the sensors over stdin/a file (advisory pass/warn; never blocks)."""
    if args.parity:
        from scripts.parity_check import run
        return run()
    from agents.sensors import list_sensors, run_sensors
    if args.list:
        print("Sensors (advisory verification manifests — report, never block):")
        for s in list_sensors():
            print(f"  • {s['name']}: {s['checks']}\n      why: {s['why']}")
        return 0
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    results = run_sensors(text)
    warned = 0
    print("Sensor results (advisory):")
    for r in results:
        icon = "✓" if r.passed else "⚠"
        warned += 0 if r.passed else 1
        print(f"  {icon} {r.sensor}: {r.detail}")
    print(f"\n{warned} warning(s). Sensors are advisory — the GovernanceGate is the enforcement "
          "floor; these are review signals, not blocks.")
    return 0


def cmd_bible(args) -> int:
    """Derive the evidence corpus (.evidence/BIBLE.md) FROM knowledge/. Required before turning
    on `evidence.provider: bible_markdown`; the corpus is a pointer into the real KB (Tenet 8),
    is gitignored, and is regenerated on demand — never hand-edited."""
    from scripts.generate_bible import main as gen_main
    argv = []
    if args.out:
        argv += ["--out", args.out]
    return gen_main(argv)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="companion", description="Governed Agentic Companion CLI")
    p.add_argument("--environment", default="dev")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="governance integrity + knowledge load").set_defaults(func=cmd_status)

    ask = sub.add_parser("ask", help="ask the governed orchestrator")
    ask.add_argument("question")
    ask.set_defaults(func=cmd_ask)

    g = sub.add_parser("gate", help="run the gate over stdin/a file")
    g.add_argument("--file", default=None)
    g.add_argument("--footer", action="store_true")
    g.set_defaults(func=cmd_gate)

    b = sub.add_parser("bible", help="derive the evidence corpus (.evidence/BIBLE.md) from knowledge/")
    b.add_argument("--out", default=None, help="output path (default: .evidence/BIBLE.md)")
    b.set_defaults(func=cmd_bible)

    au = sub.add_parser("audit", help="show the tail of the action-level audit trail")
    au.add_argument("--lines", type=int, default=20, help="number of recent events (default 20)")
    au.set_defaults(func=cmd_audit)

    fb = sub.add_parser("feedback", help="review/promote staged feedback candidates (Tenet 8)")
    fb.add_argument("action", choices=["list", "promote", "dismiss"], help="feedback action")
    fb.add_argument("--id", default=None, help="candidate id (for promote/dismiss)")
    fb.add_argument("--key", default=None, help="Tier-B knowledge key for promote (tier_b/...)")
    fb.add_argument("--summary", default=None, help="summary text for the promoted overlay entry")
    fb.add_argument("--layer", default="team", choices=["team", "project"],
                    help="memory layer for promote (team|project; precedence project>team>org)")
    fb.add_argument("--reason", default=None, help="reason for dismiss")
    fb.add_argument("--promoted", action="store_true", help="list promoted (not open) candidates")
    fb.set_defaults(func=cmd_feedback)

    pl = sub.add_parser("plan", help="staged mode: intent->plan->approve->execute (opt-in, gated)")
    pl.add_argument("action", choices=["create", "show", "approve", "step"])
    pl.add_argument("intent", nargs="?", default="", help="the intent (for create)")
    pl.add_argument("--id", default=None, help="plan id (for show/approve/step)")
    pl.set_defaults(func=cmd_plan)

    v = sub.add_parser("verify", help="advisory sensors + front-door parity/determinism guard")
    v.add_argument("--list", action="store_true", help="list the inspectable sensor manifests")
    v.add_argument("--parity", action="store_true", help="run the front-door parity guard")
    v.add_argument("--file", default=None, help="run sensors over a file (default stdin)")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
