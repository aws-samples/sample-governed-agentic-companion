"""Static checks for the diagram build surface (design D8, C30, D16; acceptance 17, 34)."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKER = {f: (ROOT / f"agentcore/app/{f}/Dockerfile").read_text() for f in ("orchestrator", "mcp_runtime")}
APK = "RUN apk add --no-cache ttf-dejavu=2.37-r9\n"
FROM_RE = re.compile(r"^FROM (\S+)(?: AS (\S+))?$", re.M)


def _froms(text):
    return FROM_RE.findall(text)


def _stage(text, name):
    start = text.index(f" AS {name}\n")
    nxt = text.find("\nFROM ", start)
    return text[start:] if nxt < 0 else text[start:nxt]


def _final(text):
    return text[text.rindex("\nFROM "):]


def test_every_from_pinned_by_digest_and_shared():
    a, b = DOCKER["orchestrator"], DOCKER["mcp_runtime"]
    assert _froms(a) == _froms(b)
    froms = _froms(a)
    assert [n for _, n in froms] == ["diagram-assets", "runtime-deps", ""]
    for ref, _ in froms:
        assert re.fullmatch(r"cgr\.dev/chainguard/python@sha256:[0-9a-f]{64}", ref), ref
    assert froms[0][0] == froms[1][0] != froms[2][0]  # latest-dev builders, latest runtime
    for text in (a, b):
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("FROM "):
                # tag comment, then the detect-secrets pragma, then the digest-pinned FROM
                assert lines[i - 1] == "# pragma: allowlist nextline secret"
                assert re.match(r"# cgr\.dev/chainguard/python:latest(-dev)? \(refreshed ", lines[i - 2])


def test_identical_builder_stage_and_lines():
    a, b = DOCKER["orchestrator"], DOCKER["mcp_runtime"]
    assert _stage(a, "diagram-assets") == _stage(b, "diagram-assets")
    for text in (a, b):
        deps, final = _stage(text, "runtime-deps"), _final(text)
        assert APK in deps
        assert "ENV GAC_DIAGRAM_ASSETS_DIR=/app/engine/diagram_assets" in final
        assert "COPY --from=diagram-assets /out/ /app/engine/diagram_assets/" in final
        assert "COPY --from=runtime-deps /app/venv /app/venv" in final
        assert "/usr/share/fonts/truetype/dejavu/" in final and "DejaVuSans-Bold.ttf" in final
        assert "PATH=/app/venv/bin:/usr/bin:/bin" in final
        assert 'ARG GAC_DIAGRAM_ICONS_SHA256=""' in text
        assert "libcairo" not in text and "cairosvg" not in text.lower()
        assert "apt-get" not in text and "useradd" not in text
        # Root-owned code, venv and catalog; only the engine's local state dirs are writable.
        assert "--chown" not in "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))
        assert "RUN chown -R 0:0 /out\n" in _stage(text, "diagram-assets")
        assert "COPY --from=runtime-deps /stage/engine/ /app/engine/" in final
        assert deps.index("pip==26.2.2") < deps.index("-r /app/requirements.txt")
        assert deps.index("-r /app/requirements.txt") < deps.index("python -m pip uninstall -y pip")
        # Every stage ends non-root; the runtime runs as the base's nonroot uid.
        for name in ("diagram-assets", "runtime-deps"):
            assert re.findall(r"^USER (\S+)$", _stage(text, name), re.M)[-1] == "65532"
        assert re.findall(r"^USER (\S+)$", final, re.M) == ["65532"]
        assert "\nRUN " not in final  # the runtime base has no shell
        assert "ENTRYPOINT []" in final


def test_requirements_pins():
    for f in ("orchestrator", "mcp_runtime"):
        req = (ROOT / f"agentcore/app/{f}/requirements.txt").read_text().lower()
        assert re.search(r"^resvg-py==0\.5\.0\b", req, re.M)
        assert "cairosvg" not in req and "pillow" not in req
    build = (ROOT / "agentcore/app/diagram-assets/requirements.txt").read_text()
    assert "pip==26.2.2" in build and "defusedxml==0.7.1" in build


def test_dockerignore():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    for rule in (".git", ".venv", "outputs", ".diagram-assets", "**/*.tfstate*",
                 "**/terraform.tfvars", ".env*"):
        assert rule in lines
    assert not [ln for ln in lines if ln.strip().startswith("agentcore/diagram-icons")]


def test_build_script():
    sh = (ROOT / "agentcore/terraform/scripts/build_and_push.sh").read_text()
    assert sh.count('"${BUILD_ARGS[@]}"') == 2 and "BUILD_ARGS=(--pull)" in sh
    assert '--build-arg "GAC_DIAGRAM_ICONS_SHA256=${GAC_DIAGRAM_ICONS_SHA256}"' in sh
    assert "ERROR: more than one .zip in agentcore/diagram-icons/" in sh
    assert "Remember: set a new image_tag for this build (ECR tags are immutable)" in sh


def test_no_subprocess_in_diagram_runtime_code():
    files = list((ROOT / "agents/diagram").glob("*.py")) + list((ROOT / "frontdoor").glob("diagram_*.py"))
    files.append(ROOT / "agents/specialists/diagram_specialist.py")
    for f in files:
        text = f.read_text()
        assert not re.search(r"^\s*(import subprocess|from subprocess)|subprocess\.|os\.system\(",
                             text, re.M), f


def test_plugin_permissions_files_server():
    perms = json.loads((ROOT / "plugin/permissions.example.json").read_text())
    assert "mcp__companion-files" in perms["permissions"]["ask"]
    mcp = json.loads((ROOT / "plugin/.mcp.json").read_text())["mcpServers"]["companion-files"]
    assert mcp["env"]["GAC_BRIDGE_UPSTREAM"] == "gateway"
    assert mcp["env"]["GAC_BRIDGE_TOOLS"] == "draw_diagram"
    agent = (ROOT / "plugin/agents/companion-diagram.md").read_text()
    assert "disallowedTools: Bash" in agent and "mcp__companion-files__draw_diagram" in agent


def test_mcp_samples_parse():
    for f in ("plugin/.mcp.json", "frontdoor/mcp.example.json"):
        json.loads((ROOT / f).read_text())


hcl2 = pytest.importorskip("hcl2")
TF = ROOT / "agentcore" / "terraform"


def _load(name):
    with open(TF / name) as fh:
        return hcl2.load(fh)


def test_terraform_variables_and_locals():
    variables = {k.strip('"'): v for blk in _load("variables.tf")["variable"] for k, v in blk.items()}
    sha = variables["diagram_icons_sha256"]
    assert sha["default"].strip('"') == "" and "^([0-9a-f]{64})?$" in json.dumps(sha["validation"])
    assert variables["diagram_enabled"]["default"] in (True, "true")
    assert "immutable" in variables["image_tag"]["description"]
    locals_ = {k.strip('"'): v for blk in _load("locals.tf")["locals"] for k, v in blk.items()}
    assert "GAC_DIAGRAM_ENABLED" in json.dumps(locals_["runtime_env"])
    build = {k.strip('"'): v for blk in _load("build.tf")["locals"] for k, v in blk.items()}
    for trig in ("orchestrator_build_trigger", "mcp_runtime_build_trigger"):
        t = json.dumps(build[trig])
        assert "diagram_icons_trigger" in t and "diagram_code_trigger" in t
        assert "diagram_icons_sha256" in t
    text = (TF / "build.tf").read_text()
    assert text.count("GAC_DIAGRAM_ICONS_SHA256 = var.diagram_icons_sha256") == 2
    # 3.4.0 (D11.5): the diagram KB (presets, vocabulary, facts) forces a rebuild too.
    code = json.dumps(build["diagram_code_trigger"])
    assert "knowledge/**/diagram/*.yaml" in code and "knowledge/**/diagram_facts.yaml" in code
