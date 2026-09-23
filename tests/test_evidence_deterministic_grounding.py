"""Regression test: the deterministic-grounding-skip failure this feature exists to close.

A deterministic/verified-zone answer at low confidence used to pass the governance gate
because the confidence bar (Tenet 4) is exploration-zone-only, so a grounded answer that
already existed in the knowledge base could be skipped in favour of first-principles
reasoning and the gate would still say "pass". The evidence gateway closes this: when a
provider is configured, a deterministic answer must be backed by a READY packet whose
evidence reopens the REAL KB file, or delivery blocks.

Condition #5 (corpus cites the real KB, not a rival ledger): the synthetic BIBLE's Evidence
line points at the actual knowledge/tier_a/security/secret_handling.yaml under an allowed
root, so a READY packet proves the real KB text was reopened.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.evidence_gateway import STATUS_READY, packet_validity_reason
from agents.evidence_providers.bible_markdown import BibleMarkdownProvider
from agents.governance_gate import gate_output, reset_gate

KB_FILE = PROJECT_ROOT / "knowledge" / "tier_a" / "security" / "secret_handling.yaml"


@pytest.fixture
def bible_citing_real_kb(tmp_path):
    """A synthetic BIBLE.md whose one claim cites the REAL secret_handling.yaml. The
    allowed roots include the repo knowledge/ tree, so reopening reads the actual KB file."""
    n_lines = len(KB_FILE.read_text(encoding="utf-8").splitlines())
    end = min(6, n_lines)
    bible = tmp_path / "BIBLE.md"
    bible.write_text(
        "## SECURITY\n\n"
        "### R-SEC-001. Credentials, tokens and private keys are never stored in the "
        "knowledge base or emitted in output; secrets live in a secret store and systems "
        "of record are read-only.\n"
        "- Class: INVARIANT\n"
        "- Authority: AUTHORIZED\n"
        f"- Evidence: {KB_FILE}:1-{end}\n",
        encoding="utf-8",
    )
    return bible, [tmp_path, KB_FILE.parent]


class TestDeterministicAnswerMustBeBackedByReopenedRealKB:
    def test_claim_reopens_the_real_kb_file(self, bible_citing_real_kb):
        bible, roots = bible_citing_real_kb
        provider = BibleMarkdownProvider(bible, allowed_roots=roots)
        packet = provider.build_packet(
            "how are credentials and secrets handled R-SEC-001", "assessment", ["security"]
        )
        assert packet.status == STATUS_READY, f"{packet.status} / {packet.gaps}"
        ref = packet.claims[0].references[0]
        assert ref.reopened is True
        assert ref.path.endswith("secret_handling.yaml")
        assert "secret" in ref.excerpt.lower()

    def test_verified_zone_answer_blocks_when_packet_is_not_ready(self, tmp_path):
        reset_gate()
        bible = tmp_path / "BIBLE.md"
        bible.write_text(
            "## SECURITY\n\n### R-SEC-999. unbacked claim.\n"
            "- Class: INVARIANT\n- Evidence: nope.yaml:1-2\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible, allowed_roots=[tmp_path])
        req = "how are secrets handled R-SEC-999"
        packet = provider.build_packet(req, "assessment", ["security"])
        assert packet.status != STATUS_READY
        out = gate_output(
            "Deterministic answer with no reopened evidence backing it.",
            agent="security", request=req, used_llm=False, confidence=0.60,
            evidence=packet, footer=False,
        )
        assert "blocked by the governance gate" in out, (
            "a deterministic answer must NOT pass unproven when a provider is configured "
            "and the packet is not READY — the exact grounding-skip the feature closes."
        )

    def test_ready_kb_backed_answer_passes(self, bible_citing_real_kb):
        reset_gate()
        bible, roots = bible_citing_real_kb
        provider = BibleMarkdownProvider(bible, allowed_roots=roots)
        req = "how are credentials and secrets handled R-SEC-001"
        packet = provider.build_packet(req, "assessment", ["security"])
        assert packet_validity_reason(packet, req, "security") == ""
        out = gate_output(
            "Secrets live in a secret store; none are stored in the KB or emitted (Tenet 6).",
            agent="security", request=req, used_llm=False, confidence=0.90,
            evidence=packet, footer=False,
        )
        assert "blocked by the governance gate" not in out
