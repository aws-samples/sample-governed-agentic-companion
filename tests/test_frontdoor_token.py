"""Tests for the gateway token helper + Cognito provider. No AWS (uses the _minter seam)."""
import io
import stat
import sys
from pathlib import Path

import pytest

FRONTDOOR = Path(__file__).resolve().parent.parent / "frontdoor"
sys.path.insert(0, str(FRONTDOOR))

from cognito_token import CognitoTokenProvider, CognitoConfigError  # noqa: E402
import gateway_token as gt                                          # noqa: E402


def _provider(seq=("t1", "t2"), ttl=3600):
    calls = {"n": 0}
    def minter():
        i = calls["n"]; calls["n"] += 1
        return seq[min(i, len(seq) - 1)], ttl
    return CognitoTokenProvider(skew_seconds=120, _minter=minter), calls


class TestProvider:
    def test_caches_until_near_expiry(self):
        p, calls = _provider()
        assert p.get_token() == "t1"
        assert p.get_token() == "t1"       # cached
        assert calls["n"] == 1

    def test_refreshes_when_expired(self):
        p, calls = _provider(("t1", "t2"), ttl=1)
        assert p.get_token() == "t1"
        assert p.get_token() == "t2"       # within skew -> re-mint
        assert calls["n"] == 2

    def test_missing_config_raises(self, monkeypatch):
        for k in ("REGION", "CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD", "USER_POOL_ID"):
            monkeypatch.delenv("GAC_COGNITO_" + k, raising=False)
        with pytest.raises(CognitoConfigError):
            CognitoTokenProvider()._cfg()


class TestTokenHelper:
    def test_upsert_replaces_and_refuses_quote(self):
        out = gt._upsert("export GAC_GATEWAY_TOKEN='old'\nexport X='keep'\n",
                         "GAC_GATEWAY_TOKEN", "new")
        assert out.count("GAC_GATEWAY_TOKEN=") == 1
        assert "export GAC_GATEWAY_TOKEN='new'" in out and "export X='keep'" in out
        with pytest.raises(ValueError):
            gt._upsert("", "GAC_GATEWAY_TOKEN", "ab'cd")

    def test_write_env_0600(self, tmp_path):
        p = tmp_path / ".env"
        gt.write_env_token("jwt", p)
        assert "export GAC_GATEWAY_TOKEN='jwt'" in p.read_text()
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    def test_print_emits_only_token(self, monkeypatch):
        monkeypatch.delenv(gt.URL_VAR, raising=False)
        p, _ = _provider()
        buf = io.StringIO()
        assert gt.run("print", provider=p, out=buf) == 0
        assert buf.getvalue() == "t1"

    def test_daemon_refreshes_and_stops(self, tmp_path, monkeypatch):
        monkeypatch.delenv(gt.URL_VAR, raising=False)
        p, calls = _provider(("a", "b"))
        sleeps = []
        rc = gt.run("daemon", provider=p, env_file=str(tmp_path / ".env"),
                    _sleep=lambda s: sleeps.append(s), _max_iterations=2)
        assert rc == 0 and calls["n"] >= 2 and len(sleeps) == 1
