"""Draw-intent routing (design D10, 9.1; acceptance 2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import Orchestrator  # noqa: E402

TABLE = [
    ("draw our private network path", "diagram"),
    ("diagram how the gateway reaches the runtimes", "diagram"),
    ("show our KB ingestion", "diagram"),
    ("show our private network path", "diagram"),
    ("please sketch the gateway path", "diagram"),
    ("generate a drawio of the request path", "diagram"),
    ("show me a diagram of the deployment", "diagram"),
    ("how do I draw a diagram?", "diagram"),
    ("show the deployment flow", "platform"),
    ("show me the network egress allowlist", "security"),
    ("show the token auth flow", "security"),
    ("show how the gateway reaches the runtimes", "platform"),
    ("what is the deployment policy?", "platform"),
    ("show me the deployment policy", "platform"),
    ("how do I rotate the token secret?", "security"),
    ("run the scan cli in dry-run", "external_tool"),
    ("drawbacks of the deployment policy?", "platform"),
    ("show me the big picture on token rotation", "security"),
    ("draw a picture of a cat", "diagram"),
]


@pytest.fixture(scope="module")
def orch():
    return Orchestrator()


@pytest.mark.parametrize("req,expected", TABLE)
def test_route(orch, req, expected):
    assert orch.route(req) == expected


def test_four_specialists_registered(orch):
    assert list(orch._specialists) == ["platform", "security", "external_tool", "diagram"]
