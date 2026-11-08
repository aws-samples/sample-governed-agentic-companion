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


_DIAGRAM_REQ = "draw our private network path"


def _diagram_doors(tmp: Path) -> dict:
    """Run one request through every diagram front door; return {door: (blocked, hashes)}."""
    import asyncio
    import hashlib
    import json
    root = Path(__file__).resolve().parent.parent
    for sub in ("frontdoor", "agentcore/app/orchestrator", "agentcore/app/mcp_runtime"):
        sys.path.insert(0, str(root / sub))
    from agents.orchestrator import Orchestrator
    import runtime_entrypoint as rt
    out = {}

    def hashes(bundle):
        return {a["file"]: a["sha256"] for a in (bundle or {}).get("artifacts", [])}

    text, bundle = Orchestrator().handle_with_artifacts(_DIAGRAM_REQ, force="diagram")
    out["engine"] = (bundle is None, hashes(bundle))
    env = rt.handle_invocation({"prompt": _DIAGRAM_REQ, "action": "diagram",
                                "include_artifacts": True})
    out["runtime"] = ("diagram" not in env, hashes(env.get("diagram")))
    plain = rt.handle_invocation({"prompt": _DIAGRAM_REQ, "action": "diagram"})
    out["manifest-rules"] = (("diagram_manifest" not in plain) == ("diagram" not in env)
                             and "diagram_manifest" not in rt.handle_invocation(
                                 {"prompt": _DIAGRAM_REQ}), {})
    try:
        import runtime_mcp_entrypoint as mrt
        inner = json.loads(mrt.draw_diagram(_DIAGRAM_REQ, include_artifacts=True))
        out["mcp"] = ("diagram" not in inner, hashes(inner.get("diagram")))
    except ImportError:
        print("  (mcp door skipped: the mcp SDK is not installed)")
    import mcp_bridge
    from runtime_client import CompanionRuntimeClient

    class _Tok:
        def get_token(self):
            return "parity"

        def invalidate(self):
            pass

    import os
    ws = tmp / "ws"
    ws.mkdir(exist_ok=True)
    os.environ["GAC_RUNTIME_ARN"] = os.environ.get("GAC_RUNTIME_ARN") or (
        "arn:aws:bedrock-agentcore:us-east-1:" + "0" * 12 + ":runtime/parity")
    client = CompanionRuntimeClient(_Tok(), _post=lambda u, h, d: (
        200, json.dumps(rt.handle_invocation(json.loads(d)))))
    os.environ["GAC_DIAGRAM_WORKSPACE_ROOT"] = str(ws)
    try:
        asyncio.run(mcp_bridge.draw_via_runtime(client, _DIAGRAM_REQ, overwrite=True))
    finally:
        os.environ.pop("GAC_DIAGRAM_WORKSPACE_ROOT", None)
    written = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (ws / "outputs" / "diagrams").rglob("*") if p.is_file()
               and p.name != ".gitignore"}
    out["bridge"] = (not written, written)
    return out


#: Natural-language fixtures (design AC 10): (request, expected outcome, expected view count).
_NL_FIXTURES = [
    ("draw gac pvt architecture end to end", "render", 4),
    ("what talks to Bedrock", "render", 1),
    ("how does memory get written", "decline", 0),
]
_PLANNER_LINE = "  Diagram planner: deterministic"


def _nl_doors(req: str) -> dict:
    """{door: (declined, [per-view {file: sha256}], planner line present)} for one request with
    the deterministic planner forced. The engine runs twice (determinism); the runtime and MCP
    doors deliver view 1 as `diagram` and any further views as `diagram_views`."""
    import json
    root = Path(__file__).resolve().parent.parent
    for sub in ("frontdoor", "agentcore/app/orchestrator", "agentcore/app/mcp_runtime"):
        sys.path.insert(0, str(root / sub))
    from agents.orchestrator import Orchestrator
    import runtime_entrypoint as rt

    def views(bundles):
        return [{a["file"]: a["sha256"] for a in b.get("artifacts", [])} for b in bundles]

    out = {}
    bundles = []
    for door in ("engine", "engine-again"):
        text, bundles = Orchestrator().handle_with_views(req, force="diagram",
                                                         planner="deterministic")
        out[door] = (not bundles, views(bundles), _PLANNER_LINE in text.split("\n"))
    env = rt.handle_invocation({"prompt": req, "action": "diagram", "include_artifacts": True,
                                "planner": "deterministic"})
    got = ([env["diagram"]] if "diagram" in env else []) + list(env.get("diagram_views", []))
    out["runtime"] = ("diagram" not in env, views(got),
                      _PLANNER_LINE in str(env.get("response", "")).split("\n"))
    try:
        import runtime_mcp_entrypoint as mrt
        inner = json.loads(mrt.draw_diagram(req, include_artifacts=True, planner="deterministic"))
        got = ([inner["diagram"]] if "diagram" in inner else []) + \
            list(inner.get("diagram_views", []))
        out["mcp"] = ("diagram" not in inner, views(got),
                      _PLANNER_LINE in str(inner.get("response", "")).split("\n"))
    except ImportError:
        print("  (mcp door skipped: the mcp SDK is not installed)")
    bridge = _nl_bridge(req, rt, [b.get("name") for b in bundles])
    if bridge is None:
        print("  (bridge door skipped: anyio is not installed)")
    else:
        out["bridge"] = bridge
    return out


def _nl_bridge(req: str, rt, names: list):
    """The bridge door: through the runtime client seam into handle_invocation, then the local
    writer; per-view checksums of the files it actually wrote (in the engine's view order)."""
    import asyncio
    import hashlib
    import json
    import os
    import tempfile
    try:
        import anyio  # noqa: F401 - the bridge's runtime path runs the call in a worker thread
    except ImportError:
        return None
    import mcp_bridge
    from runtime_client import CompanionRuntimeClient

    class _Tok:
        def get_token(self):
            return "parity"

        def invalidate(self):
            pass

    client = CompanionRuntimeClient(_Tok(), _post=lambda u, h, d: (
        200, json.dumps(rt.handle_invocation(json.loads(d)))))
    saved_root = os.environ.get("GAC_DIAGRAM_WORKSPACE_ROOT")
    os.environ["GAC_RUNTIME_ARN"] = os.environ.get("GAC_RUNTIME_ARN") or (
        "arn:aws:bedrock-agentcore:us-east-1:" + "0" * 12 + ":runtime/parity")
    with tempfile.TemporaryDirectory(prefix="gac-parity-nl-") as t:
        os.environ["GAC_DIAGRAM_WORKSPACE_ROOT"] = t
        try:
            res = asyncio.run(mcp_bridge.draw_via_runtime(client, req, overwrite=True,
                                                          planner="deterministic"))
        finally:
            if saved_root is None:
                os.environ.pop("GAC_DIAGRAM_WORKSPACE_ROOT", None)
            else:
                os.environ["GAC_DIAGRAM_WORKSPACE_ROOT"] = saved_root
        base = Path(t) / "outputs" / "diagrams"
        got = [{p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((base / n).iterdir()) if p.is_file()}
               for n in names if (base / n).is_dir()]
    return (res.declined, got, _PLANNER_LINE in res.text.split("\n"))


def run_nl() -> int:
    """AC 10: the natural-language fixtures reach the same verdict and the same per-view
    checksums at every in-process door (engine twice, runtime, MCP and the bridge's saved
    files), every view delivered, and carry the deterministic planner footer line."""
    failures = 0
    print("Natural-language diagram fixtures (deterministic planner)")
    for req, expect, n_views in _NL_FIXTURES:
        doors = _nl_doors(req)
        ref = doors["engine"][1]
        for door, (declined, vs, line) in doors.items():
            same = vs == ref
            ok = (declined == (expect == "decline")) and same and line and len(vs) == n_views
            failures += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} {req[:36]:36} {door:12} declined={declined} "
                  f"views={len(vs)} same-checksums={bool(same)} planner-line={line}")
    return failures


def run_diagram() -> int:
    """Diagram front doors: same verdict and identical artefact hashes; a blocked fixture blocks
    at every door. Skipped (with a note) when defusedxml is not installed."""
    import tempfile
    try:
        import defusedxml  # noqa: F401
    except ImportError:
        print("  (diagram section skipped: defusedxml is not installed)")
        return 0
    from agents.diagram import assets as A
    from agents.specialists import diagram_specialist as DS
    from scripts.diagram_fixture import build_fixture_catalog
    failures = 0
    print("Diagram front doors (test-fixture icons)")
    saved_dir, saved_env = A.LOCAL_ASSETS_DIR, os_environ_pop("GAC_DIAGRAM_ASSETS_DIR")
    with tempfile.TemporaryDirectory(prefix="gac-parity-") as t:
        tmp = Path(t)
        build_fixture_catalog(tmp / "assets")
        A.LOCAL_ASSETS_DIR = tmp / "assets"
        try:
            doors = _diagram_doors(tmp)
            ref = doors["engine"][1]
            for door, (blocked, h) in doors.items():
                ok = h == ref if door != "manifest-rules" else blocked is True
                ok = ok and (door == "manifest-rules" or not blocked)
                failures += 0 if ok else 1
                print(f"  {'✓' if ok else '✗'} {door:15} blocked={blocked if door != 'manifest-rules' else '-'} "
                      f"artefacts={len(h)} same-hashes={h == ref if door != 'manifest-rules' else '-'}")
            original = DS.DiagramSpecialist.render_request
            DS.DiagramSpecialist.render_request = lambda self, *a, **k: DS.RenderResult(
                "I have deployed the stack to production.", {"schema": "gac.diagram/1"}, ["x"])
            try:
                (tmp / "b").mkdir()
                blocked_doors = _diagram_doors(tmp / "b")
            finally:
                DS.DiagramSpecialist.render_request = original
            for door, (blocked, h) in blocked_doors.items():
                if door == "manifest-rules":
                    continue
                ok = blocked and not h
                failures += 0 if ok else 1
                print(f"  {'✓' if ok else '✗'} blocked fixture at {door:9} blocked={blocked}")
            failures += run_nl()
        finally:
            A.LOCAL_ASSETS_DIR = saved_dir
            if saved_env is not None:
                import os
                os.environ["GAC_DIAGRAM_ASSETS_DIR"] = saved_env
    return failures


def os_environ_pop(key):
    import os
    return os.environ.pop(key, None)


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
    failures += run_diagram()
    if failures:
        print(f"\n⛔ parity check FAILED: {failures} fixture(s) mismatched.")
        return 1
    print("\n✅ parity check passed: all front doors agree, deterministic, and correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
