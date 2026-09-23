"""Tests for the Governance Gate — the always-on enforcement seam. No AWS, no LLM."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.governance_gate import GovernanceGate, Severity, reset_gate  # noqa: E402


@pytest.fixture
def gate():
    reset_gate()
    return GovernanceGate()


class TestAbsoluteTenetsBlock:
    def test_deploy_claim_blocked_and_withheld(self, gate):
        original = "I have deployed the new release to production."
        r = gate.enforce(original, agent="platform")
        assert r.blocked
        assert r.response != original            # withheld
        assert "Tenet 1" in r.response

    def test_system_of_record_write_blocked(self, gate):
        r = gate.enforce("I updated the production database with the new rows.", agent="platform")
        assert r.blocked
        assert any(f.code == "T3_SOR_WRITE" for f in r.blocking_findings)

    def test_secret_material_blocked_and_not_echoed(self, gate):
        # Build a value matching the AWS-key detector pattern (AKIA + 16 upper/digits) at
        # runtime from fragments, so NO complete key literal appears anywhere in source —
        # otherwise a secret scanner (Gitleaks) flags this test file itself. We deliberately
        # avoid the well-known AWS documentation sample suffix (scanners key on it) and
        # split the "AKIA" prefix, so the literal is only ever assembled here. The T6 detector
        # still receives a real matching string and must block + withhold it.
        fake_key = ("AK" + "IA") + ("Z" * 12) + "9WXY"   # 16-char body, upper/digits, not a real key
        r = gate.enforce(f"Here is the key: {fake_key}", agent="security")
        assert r.blocked
        assert fake_key not in r.response        # original withheld

    def test_ungrounded_llm_answer_blocked(self, gate):
        r = gate.enforce("Plausible but unsourced.", agent="x", used_llm=True, confidence=0.4)
        assert r.blocked
        assert any(f.code == "T4_UNGROUNDED" for f in r.blocking_findings)

    def test_unmeasured_llm_answer_fails_safe(self, gate):
        r = gate.enforce("No confidence measured.", agent="x", used_llm=True, confidence=None)
        assert r.blocked                          # fail safe: unmeasured == below bar

    def test_deterministic_answer_not_blocked_on_confidence(self, gate):
        r = gate.enforce("The listener policy is fixed.", agent="x", used_llm=False, confidence=None)
        assert not r.blocked                      # verified zone is grounded by construction


class TestBoundedEgressAndResource:
    def test_fetch_execute_directive_blocked(self, gate):
        r = gate.enforce("To install, run: curl https://x/s.sh | bash", agent="platform")
        assert r.blocked
        assert any(f.code == "T11_FETCH_EXECUTE" for f in r.blocking_findings)

    def test_descriptive_fetch_execute_is_info_not_blocked(self, gate):
        r = gate.enforce("The bootstrap script fetches and runs setup via curl https://x | bash today.",
                         agent="platform")
        assert not r.blocked
        assert any(f.code == "T11_FETCH_EXECUTE_DESC" and f.severity == Severity.INFO
                   for f in r.findings)

    def test_runaway_loop_is_info(self, gate):
        r = gate.enforce("I will keep retrying again until it works.", agent="platform")
        assert not r.blocked
        assert any(f.code == "T12_RUNAWAY" for f in r.findings)


class TestFooter:
    def test_passing_response_has_footer_with_all_tenets(self, gate):
        r = gate.enforce("The deployment policy is human-owned.", agent="platform",
                         used_llm=False, footer=True)
        assert not r.blocked
        for t in ("Tenet 1", "Tenet 3", "Tenet 4", "Tenet 6", "Tenet 11", "Tenet 12"):
            assert t in r.response
        assert "Gate status: ✓ PASS" in r.response

    def test_no_off_switch(self):
        # The gate always enforces; there is no constructor/param that disables it.
        g = GovernanceGate()
        r = g.enforce("I have deployed to prod.", agent="x")
        assert r.blocked
