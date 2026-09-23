"""Pytest configuration.

The evidence gateway ships OFF by default in this kit (config.yaml `evidence.provider: none`),
so the deterministic path and the default `ask`/`gate` behaviour need no corpus. The evidence
and provider tests, however, opt INTO the `bible_markdown` provider and need a corpus to resolve
against. The documented way to produce one is `./run.sh bible`, which derives .evidence/BIBLE.md
from the real knowledge/ tree (a pointer into the KB, not a rival ledger — Tenet 8).

This session-autouse fixture derives that corpus once before tests run, so the evidence-on tests
exercise the SAME path a user gets after `./run.sh bible` — not a special bypassed mode. It is
idempotent and cheap; tests that want the no-provider or fail-closed paths configure that
themselves.
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _generate_evidence_corpus():
    """Derive .evidence/BIBLE.md from knowledge/ so the evidence-on tests have a corpus to
    resolve (pointer into the real KB; Tenet 8). Idempotent and cheap."""
    try:
        from scripts.generate_bible import generate
        out = _ROOT / ".evidence" / "BIBLE.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generate(), encoding="utf-8")
    except Exception:
        # If generation fails, tests that need the corpus will fail loudly (fail-closed);
        # the fixture itself must not mask that by raising here.
        pass
    yield
