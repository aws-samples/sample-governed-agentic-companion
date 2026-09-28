"""Tests for the unified data-protection guard (Tenet 6). No AWS, no LLM.

Secret-SHAPED inputs are ASSEMBLED AT RUNTIME from fragments so no complete secret-shaped
literal ever appears in source (nothing for a secret scanner to flag). Each builder proves the
guard rejects/redacts a class of secret or PII while PRESERVING operational config.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.data_protection import scrub_or_reject  # noqa: E402


def _secret_inputs():
    """Secret-shaped values built from fragments (no literal in source)."""
    aws_key = "AK" + "IA" + "ABCDEFGHIJKLMNOP"          # AWS-access-key shape, synthetic
    pw = "hunter" + "2xyz"                                # bare password value
    client_secret = "abcd" + "1234" + "efgh" + "5678"    # client_secret= value
    conn_pw = "S3cret" + "Pw0rd"                          # password in a connection string
    pem_header = "-----BEGIN " + "RSA" + " PRIVATE" + " KEY" + "-----"  # PEM header marker only
    api_key = "sk-" + "abcdef123456ghijkl"               # api key value
    jwt = ".".join(["eyJ" + "hbGciOiJodqweqwe", "eyJ" + "zdWIiOiIxMjM0NTY", "SflKxwRJSMeKKF2QT"])
    return {
        "aws_key": f"set {aws_key} as the id",
        "password": f"connect with password: {pw}",
        "client_secret": f"client_secret={client_secret}",
        "conn_string": f"jdbc://svc:{conn_pw}@db.internal:5432/app",
        "private_key": f"key is {pem_header}",
        "api_key": f"api_key: {api_key}",
        "jwt": f"bearer: {jwt}",
    }


class TestSecretsRejected:
    @pytest.mark.parametrize("kind", [
        "aws_key", "password", "client_secret", "conn_string", "private_key", "api_key", "jwt",
    ])
    def test_secret_rejects_write(self, kind):
        r = scrub_or_reject(_secret_inputs()[kind], stage="learn")
        assert r.rejected is True
        assert r.cleaned == ""  # nothing safe to persist
        joined = " ".join(r.reasons).lower()
        assert "hunter" not in joined and "s3cret" not in joined  # secret value never echoed


class TestOperationalConfigPreserved:
    """The guard must NOT touch operational config — that is the KB's value."""

    @pytest.mark.parametrize("text", [
        "ports 8080 8443 9000 open on the gateway",
        "topic=orders queue=events poolSize=10 timeout=30",
        "listener tcp:*:5023 for the service",
        "metadata endpoint at 169.254.169.254",
        "connect to db.internal:5432 with pool size 20",
    ])
    def test_operational_config_is_allowed_untouched(self, text):
        r = scrub_or_reject(text, stage="learn")
        assert r.ok is True and r.cleaned == text


class TestPiiRedacted:
    def test_email_redacted_but_rest_kept(self):
        r = scrub_or_reject("ping alice@example.com about the 8080 rollout", stage="task-summary")
        assert r.ok is True
        assert "alice@example.com" not in r.cleaned and "8080 rollout" in r.cleaned

    def test_labeled_phone_redacted(self):
        r = scrub_or_reject("oncall mobile: +1 415 555 0132 for escalation", stage="learn")
        assert r.ok is True and "555 0132" not in r.cleaned

    def test_bare_port_list_not_treated_as_phone(self):
        r = scrub_or_reject("ports 10100 10200 10300 10400 open", stage="learn")
        assert r.ok is True and r.cleaned == "ports 10100 10200 10300 10400 open"


class TestModes:
    def test_redact_mode_never_rejects(self, monkeypatch):
        monkeypatch.setenv("GAC_DATA_PROTECTION_MODE", "redact")
        pw = "hunter" + "2xyz"
        r = scrub_or_reject(f"password: {pw}", stage="learn")
        assert r.ok is True and pw not in r.cleaned

    def test_strict_mode_rejects_pii_too(self, monkeypatch):
        monkeypatch.setenv("GAC_DATA_PROTECTION_MODE", "strict")
        r = scrub_or_reject("contact bob@example.com", stage="learn")
        assert r.rejected is True

    def test_balanced_default(self, monkeypatch):
        monkeypatch.delenv("GAC_DATA_PROTECTION_MODE", raising=False)
        pw = "hunter" + "2xyz"
        assert scrub_or_reject(f"password: {pw}", stage="x").rejected is True   # secret
        assert scrub_or_reject("email bob@example.com", stage="x").ok is True    # pii redacted


class TestProprietaryTerms:
    def test_configured_term_redacted(self, monkeypatch):
        monkeypatch.setenv("GAC_PROPRIETARY_TERMS", "Acme,ProjectFoo")
        r = scrub_or_reject("the Acme rollout needs a config change", stage="task-summary")
        assert r.ok is True and "Acme" not in r.cleaned and "config change" in r.cleaned

    def test_no_terms_configured_is_noop(self, monkeypatch):
        monkeypatch.delenv("GAC_PROPRIETARY_TERMS", raising=False)
        r = scrub_or_reject("the Acme rollout needs a config change", stage="learn")
        assert r.cleaned == "the Acme rollout needs a config change"


class TestReDoSBounds:
    def test_large_benign_input_is_fast(self):
        import time
        big = ("port: 8080 topic=orders tcp:*:5023 ") * 20000
        t0 = time.time()
        r = scrub_or_reject(big, stage="learn")
        assert (time.time() - t0) < 1.0  # bounded scan, no catastrophic backtracking
        assert r.ok is True

    def test_secret_still_detected_after_bounding(self):
        aws_key = "AK" + "IA" + "1234567890ABCDEF"
        assert scrub_or_reject(aws_key, stage="t").rejected
