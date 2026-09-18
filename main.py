#!/usr/bin/env python3
"""Governed Agentic Companion — CLI front door.

Commands:
  status                 governance integrity check + knowledge load summary
  ask "<question>"       route a question through the governed orchestrator (gated)
  gate [--file F]        run the governance gate over stdin/a file (front-door backstop)

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
    from agents.governance_gate import get_gate
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    result = get_gate().enforce(text, agent="front-door", request="", footer=args.footer)
    print(result.response)
    return 2 if result.blocked else 0


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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
