"""Front-door parity / determinism guard — the GAC analogue of AI-DLC's `package.ts --check`.

AI-DLC builds twice in independent roots and byte-compares to prove its generator is
deterministic. GAC's analogue proves the thing that matters here: EVERY front door that runs the
governance gate reaches the SAME verdict for the same input, and the gate is deterministic
(same input -> same verdict, every time). If the CLI `gate`, the MCP `govern()`, and the raw
GovernanceGate ever disagreed, "the gate is the same everywhere, unbypassable" would be false.

This is a pure, stdlib-only self-check (no AWS, no MCP SDK, no subprocess required for the
in-process paths). It returns 0 on parity, 1 on a mismatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fixtures: (label, text, used_llm, expected_blocked). Cover each absolute tenet + a clean pass.
_FIXTURES = [
    ("t1_deploy_claim", "I have deployed the stack to production.", False, True),
    ("t3_sor_write", "I updated the production database with the new rows.", False, True),
    ("t6_secret", "password = s3cr3tLongValueThatLooksReal", False, True),
    ("clean_artifact", "Here is a reviewed Terraform plan for a human to apply after review.",
     False, False),
    ("t4_ungrounded_llm", "The answer is probably 42, I think.", True, True),  # llm + no conf
]


def _verdict_raw_gate(text: str, used_llm: bool):
    from agents.governance_gate import GovernanceGate
    # Fresh instance (determinism must not depend on shared state).
    r = GovernanceGate().enforce(text, agent="parity", request="", footer=False,
                                 used_llm=used_llm,
                                 confidence=None if used_llm else None)
    return r.blocked


def _verdict_cli_gate(text: str, used_llm: bool):
    # The CLI path uses the shared gate via get_gate(); mirror its call (footer off).
    from agents.governance_gate import get_gate
    r = get_gate().enforce(text, agent="front-door", request="", footer=False,
                           used_llm=used_llm)
    return r.blocked


def _verdict_mcp(text: str, used_llm: bool):
    from frontdoor.gate_mcp_server import govern
    return govern(text, used_llm=used_llm)["blocked"]


def run() -> int:
    front_doors = [
        ("raw-gate", _verdict_raw_gate),
        ("cli-gate", _verdict_cli_gate),
        ("mcp-govern", _verdict_mcp),
    ]
    failures = 0
    print("Front-door parity / determinism guard")
    for label, text, used_llm, expected in _FIXTURES:
        verdicts = {}
        for fd_name, fn in front_doors:
            try:
                verdicts[fd_name] = fn(text, used_llm)
            except Exception as exc:
                verdicts[fd_name] = f"ERROR:{exc}"
        # Determinism: run the raw gate twice; must agree.
        twice = _verdict_raw_gate(text, used_llm) == _verdict_raw_gate(text, used_llm)
        agree = len({v for v in verdicts.values()}) == 1
        correct = verdicts.get("raw-gate") == expected
        ok = agree and twice and correct
        failures += 0 if ok else 1
        status = "✓" if ok else "✗"
        print(f"  {status} {label:20} blocked={verdicts.get('raw-gate')} "
              f"(expected {expected}) | parity={agree} deterministic={twice} | {verdicts}")
    if failures:
        print(f"\n⛔ parity check FAILED: {failures} fixture(s) mismatched.")
        return 1
    print("\n✅ parity check passed: all front doors agree, deterministic, and correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
