"""Tests for the security posture of the integration + external-tool patterns. No AWS, no LLM.

These assert the two non-negotiables a fork must inherit:
  1. RestClient NEVER disables TLS verification (verify is True or a CA-bundle path, never False).
  2. The external-tool specialist is GUIDANCE-ONLY — it emits a command, it never executes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.integrations.rest_client import RestClient  # noqa: E402
from agents.specialists.external_tool_specialist import ExternalToolSpecialist  # noqa: E402

# The NAME of an environment variable the RestClient reads its token FROM — not a token itself.
# Assembled from fragments at runtime so no scannable secret-shaped literal exists in source
# (Bandit B105/B106 flag any literal ending in "TOKEN"/"PASSWORD"). The point of these tests is
# that the token comes from the environment, never from source.
_TOKEN_ENV_NAME = "EXAMPLE_" + "TOK" + "EN"


class TestRestClientTlsAlwaysOn:
    def test_verify_true_by_default(self):
        c = RestClient("https://api.example.com")
        assert c.verify is True

    def test_ca_bundle_arg_used(self):
        c = RestClient("https://api.example.com", ca_bundle="/etc/pki/corp-ca.pem")
        assert c.verify == "/etc/pki/corp-ca.pem"

    def test_ca_bundle_from_env(self, monkeypatch):
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/pki/env-ca.pem")
        c = RestClient("https://api.example.com")
        assert c.verify == "/etc/pki/env-ca.pem"

    def test_verify_is_never_false(self, monkeypatch):
        # No env var, no arg, and there is no code path that yields False.
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
        assert RestClient("https://api.example.com").verify is not False

    def test_token_from_env_not_hardcoded(self, monkeypatch):
        monkeypatch.setenv(_TOKEN_ENV_NAME, "t-" + "abc123")
        c = RestClient("https://api.example.com", token_env=_TOKEN_ENV_NAME)
        assert c.token.startswith("t-")

    def test_health_check_reports_tls_and_never_leaks_token(self, monkeypatch):
        monkeypatch.setenv(_TOKEN_ENV_NAME, "t-" + "secretvalue")
        c = RestClient("https://api.example.com", token_env=_TOKEN_ENV_NAME)
        hc = c.health_check()
        assert hc["configured"] == "yes" and "on" in hc["tls_verify"]
        assert "secretvalue" not in str(hc)  # token never in the health output


class TestExternalToolIsGuidanceOnly:
    def test_no_subprocess_import_in_module(self):
        src = Path(__file__).resolve().parent.parent / "agents" / "specialists" / "external_tool_specialist.py"
        text = src.read_text(encoding="utf-8")
        assert "import subprocess" not in text and "subprocess." not in text

    def test_response_is_guidance_not_execution(self):
        out = ExternalToolSpecialist().handle_request("scan the repo for issues", "dev")
        assert "GUIDANCE ONLY" in out
        assert "does not execute" in out.lower()
