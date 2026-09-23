"""Tests for the optional BIBLE-markdown evidence provider
(agents/evidence_providers/bible_markdown.py).

Uses only synthetic, tmp_path-local BIBLE.md fixtures and source files, never Darren's
real BIBLE, vault path, or any real host/account/job identifier.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.evidence_gateway import (
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_READY,
    STATUS_STOP,
    verify_packet,
)
from agents.evidence_providers.bible_markdown import BibleMarkdownProvider

SYNTHETIC_BIBLE = """\
Version: 1.0 | Generated: 2026-01-01

## WIDGET CONFIGURATION

### R-WIDGET-1. The widget timeout is 30 seconds.
- Class: INVARIANT
- Authority: AUTHORIZED
- Evidence: source.txt:1-2

### R-WIDGET-2. The gadget color was blue as of 2026-01-01.
- Class: POINT-IN-TIME
- AsOf: 2026-01-01
- Evidence: source.txt:3-4

### R-WIDGET-3. The sprocket vendor is disputed.
- Class: CONTESTED
- Position: vendor A says X
- Position: vendor B says Y
- Evidence: source.txt:5-6

### R-WIDGET-4. The legacy flywheel mode is deprecated.
- Class: SUPERSEDED
- Evidence: source.txt:7-8

### R-WIDGET-5. No reachable evidence for this bearing claim.
- Class: INVARIANT
- Evidence: missing-file.txt:1-2
"""

SYNTHETIC_SOURCE = "\n".join(f"line {i}" for i in range(1, 20))


@pytest.fixture
def bible_dir(tmp_path):
    (tmp_path / "source.txt").write_text(SYNTHETIC_SOURCE, encoding="utf-8")
    bible_path = tmp_path / "BIBLE.md"
    bible_path.write_text(SYNTHETIC_BIBLE, encoding="utf-8")
    return tmp_path, bible_path


class TestBibleMarkdownProviderPacket:
    def test_invariant_claim_with_reopened_evidence_is_ready(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("what is the widget timeout", "analyze", ["platform"])
        assert packet.status == STATUS_READY
        assert verify_packet(packet.to_dict())
        claim = next(c for c in packet.claims if c.claim_id == "R-WIDGET-1")
        assert claim.references[0].reopened is True
        assert claim.references[0].digest

    def test_contested_claim_produces_stop(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("what is the widget vendor R-WIDGET-3", "analyze", ["platform"])
        assert packet.status == STATUS_STOP
        assert any("CONTESTED" in s for s in packet.stop_conditions)

    def test_superseded_claim_cannot_authorize_work(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("widget legacy mode R-WIDGET-4", "analyze", ["platform"])
        assert packet.status == STATUS_STOP
        assert not packet.can_dispatch

    def test_point_in_time_claim_requires_live_check_for_current_request(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("what is the current widget color now R-WIDGET-2",
                                        "analyze", ["platform"])
        assert packet.live_checks

    def test_point_in_time_claim_requires_live_check_for_other_current_state_wording(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("is the widget colour still R-WIDGET-2 up to date",
                                        "analyze", ["platform"])
        assert packet.live_checks

    def test_claim_with_unreachable_evidence_is_a_gap(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("no evidence claim R-WIDGET-5", "analyze", ["platform"])
        assert packet.status == STATUS_INSUFFICIENT_EVIDENCE
        assert packet.gaps

    def test_no_matching_claims_is_insufficient_evidence(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("totally unrelated request about spaceships",
                                        "analyze", ["platform"])
        assert packet.status == STATUS_INSUFFICIENT_EVIDENCE

    def test_discontiguous_line_citation_is_not_collapsed(self, bible_dir):
        tmp_path, bible_path = bible_dir
        (tmp_path / "multi.txt").write_text(SYNTHETIC_SOURCE, encoding="utf-8")
        bible_path.write_text(
            "## SECTION\n\n### R-MULTI-1. multi-range claim.\n"
            "- Class: INVARIANT\n- Evidence: multi.txt:1-2,10-11\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("multi-range claim R-MULTI-1", "analyze", ["platform"])
        ref = packet.claims[0].references[0]
        assert ref.line_ranges == ((1, 2), (10, 11))
        assert "line 1" in ref.excerpt and "line 10" in ref.excerpt
        assert "line 5" not in ref.excerpt

    def test_evidence_outside_allowed_roots_is_withheld(self, bible_dir, tmp_path_factory):
        tmp_path, bible_path = bible_dir
        outside_dir = tmp_path_factory.mktemp("outside")
        outside_file = outside_dir / "secret_source.txt"
        outside_file.write_text("outside content", encoding="utf-8")
        bible_path.write_text(
            f"## SECTION\n\n### R-OUT-1. outside claim.\n"
            f"- Class: INVARIANT\n- Evidence: {outside_file}:1-1\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("outside claim R-OUT-1", "analyze", ["platform"])
        ref = packet.claims[0].references[0]
        assert ref.reopened is False
        assert ref.error

    def test_secret_pattern_in_evidence_is_redacted(self, bible_dir):
        tmp_path, bible_path = bible_dir
        (tmp_path / "creds.txt").write_text("token: sk-realvalue123", encoding="utf-8")
        bible_path.write_text(
            "## SECTION\n\n### R-SECRET-1. secret claim.\n"
            "- Class: INVARIANT\n- Evidence: creds.txt:1-1\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("secret claim R-SECRET-1", "analyze", ["platform"])
        ref = packet.claims[0].references[0]
        assert "sk-realvalue123" not in ref.excerpt
        assert "[REDACTED]" in ref.excerpt


def _noisy_corpus_claim(n: int, distinct_word: str, claim_class: str = "INVARIANT") -> str:
    """One claim sharing generic domain vocabulary with every other claim in the
    corpus, plus one claim-specific word. Reproduces the real-BIBLE failure mode
    (many claims scored equally on "cloud"/"environment"/"system"/"server" alone)
    without copying any private BIBLE content.
    """
    return (
        f"### R-NOISY-{n}. The cloud environment system server for {distinct_word} "
        f"is configured.\n- Class: {claim_class}\n- Evidence: source.txt:1-1\n"
    )


@pytest.fixture
def noisy_corpus_dir(tmp_path):
    (tmp_path / "source.txt").write_text("line 1\n", encoding="utf-8")
    body = "## NOISY SECTION\n\n" + "\n".join(
        _noisy_corpus_claim(i, word)
        for i, word in enumerate(
            ["widget", "gadget", "sprocket", "flywheel", "bearing", "gearbox",
             "turbine", "pulley", "bracket", "coupling", "manifold", "actuator"]
        )
    )
    # One CONTESTED and one SUPERSEDED claim in the same noisy corpus, so the
    # regression proves they are excluded by relevance, not by chance absence.
    body += _noisy_corpus_claim(100, "camshaft", claim_class="CONTESTED")
    body += _noisy_corpus_claim(101, "flange", claim_class="SUPERSEDED")
    bible_path = tmp_path / "BIBLE.md"
    bible_path.write_text(body, encoding="utf-8")
    return tmp_path, bible_path


class TestClaimSelectionAgainstNoisyCorpus:
    """Reproduces the real-BIBLE failure: many claims sharing generic domain words
    ("cloud", "environment", "system", "server") must not all get swept into the
    packet alongside an explicit, on-topic claim ID."""

    def test_generic_domain_words_alone_select_nothing(self, noisy_corpus_dir):
        tmp_path, bible_path = noisy_corpus_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet(
            "what is the cloud environment system server configuration", "analyze", ["platform"]
        )
        assert packet.claims == ()
        assert packet.status == STATUS_INSUFFICIENT_EVIDENCE

    def test_explicit_claim_id_selects_exactly_that_claim_despite_shared_noise(self, noisy_corpus_dir):
        tmp_path, bible_path = noisy_corpus_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet(
            "what is the cloud environment system server for R-NOISY-3", "analyze", ["platform"]
        )
        assert [c.claim_id for c in packet.claims] == ["R-NOISY-3"]
        assert packet.status == STATUS_READY

    def test_explicit_claim_id_match_is_word_boundary_not_substring(self, tmp_path):
        """R-A-1 must not also select R-A-10 (word-boundary, not substring)."""
        (tmp_path / "source.txt").write_text("line 1\n", encoding="utf-8")
        bible_path = tmp_path / "BIBLE.md"
        bible_path.write_text(
            "## SECTION\n\n"
            "### R-A-1. first claim.\n- Class: INVARIANT\n- Evidence: source.txt:1-1\n\n"
            "### R-A-10. tenth claim.\n- Class: INVARIANT\n- Evidence: source.txt:1-1\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("about R-A-1 only", "analyze", ["platform"])
        assert [c.claim_id for c in packet.claims] == ["R-A-1"]

    def test_selection_is_stable_across_repeated_calls(self, noisy_corpus_dir):
        tmp_path, bible_path = noisy_corpus_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        request = "what is the cloud environment system server for widget and gadget"
        first = [c.claim_id for c in provider.build_packet(request, "analyze", ["platform"]).claims]
        second = [c.claim_id for c in provider.build_packet(request, "analyze", ["platform"]).claims]
        assert first == second

    def test_substring_of_a_request_word_does_not_count_as_a_token_match(self, tmp_path):
        """"turbines" in an assertion must not match a request token "turbine" via
        substring; only a complete, normalized token counts."""
        (tmp_path / "source.txt").write_text("line 1\n", encoding="utf-8")
        bible_path = tmp_path / "BIBLE.md"
        bible_path.write_text(
            "## SECTION\n\n### R-SUB-1. The turbines require maintenance.\n"
            "- Class: INVARIANT\n- Evidence: source.txt:1-1\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("check the turbine status", "analyze", ["platform"])
        assert packet.claims == ()

    def test_contested_and_superseded_claims_are_not_swept_in_by_shared_noise(self, noisy_corpus_dir):
        tmp_path, bible_path = noisy_corpus_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet(
            "what is the cloud environment system server for R-NOISY-3", "analyze", ["platform"]
        )
        assert not any(c.claim_class in ("CONTESTED", "SUPERSEDED") for c in packet.claims)


class TestUsableEvidenceInvariants:
    def test_selected_claim_with_no_evidence_citation_is_a_gap(self, tmp_path):
        (tmp_path / "source.txt").write_text("line 1\n", encoding="utf-8")
        bible_path = tmp_path / "BIBLE.md"
        bible_path.write_text(
            "## SECTION\n\n### R-NOCITE-1. a claim with no evidence line at all.\n"
            "- Class: INVARIANT\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("R-NOCITE-1", "analyze", ["platform"])
        assert packet.status == STATUS_INSUFFICIENT_EVIDENCE
        assert any("R-NOCITE-1" in g for g in packet.gaps)
        assert packet.claims[0].references == ()

    def test_ready_packet_always_has_at_least_one_selected_claim(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("R-WIDGET-1", "analyze", ["platform"])
        assert packet.status == STATUS_READY
        assert len(packet.claims) >= 1

    def test_every_claim_in_a_ready_packet_has_a_reopened_reference(self, tmp_path):
        (tmp_path / "a.txt").write_text("line 1\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("line 1\n", encoding="utf-8")
        bible_path = tmp_path / "BIBLE.md"
        bible_path.write_text(
            "## SECTION\n\n"
            "### R-MULTI-1. first.\n- Class: INVARIANT\n- Evidence: a.txt:1-1\n\n"
            "### R-MULTI-2. second.\n- Class: INVARIANT\n- Evidence: b.txt:1-1\n",
            encoding="utf-8",
        )
        provider = BibleMarkdownProvider(bible_path, allowed_roots=[tmp_path])
        packet = provider.build_packet("R-MULTI-1 R-MULTI-2", "analyze", ["platform"])
        assert packet.status == STATUS_READY
        for claim in packet.claims:
            assert any(r.reopened for r in claim.references)


class TestSplitRootList:
    """GAC_EVIDENCE_ROOTS parsing (see _split_root_list in bible_markdown.py)."""

    def test_single_windows_drive_path_is_not_split_at_the_colon(self):
        from agents.evidence_providers.bible_markdown import _split_root_list
        assert _split_root_list(r"C:\Users\bdrn\vault") == [r"C:\Users\bdrn\vault"]

    def test_two_windows_drive_paths_separated_by_semicolon(self):
        from agents.evidence_providers.bible_markdown import _split_root_list
        assert _split_root_list(r"C:\Users\a;D:\Users\b") == [r"C:\Users\a", r"D:\Users\b"]

    def test_posix_multi_root_still_splits_on_colon(self):
        from agents.evidence_providers.bible_markdown import _split_root_list
        assert _split_root_list("/a/b:/c/d") == ["/a/b", "/c/d"]

    def test_single_posix_path_unaffected(self):
        from agents.evidence_providers.bible_markdown import _split_root_list
        assert _split_root_list("/a/b") == ["/a/b"]

    def test_windows_roots_via_from_env(self, tmp_path):
        bible_path = tmp_path / "BIBLE.md"
        bible_path.write_text("## SECTION\n\n### R-A-1. a claim.\n- Class: INVARIANT\n",
                              encoding="utf-8")
        # Not a real Windows path on this test host, but proves from_env routes the
        # raw env var value through _split_root_list rather than the old ":"-splitting
        # regex, using an unambiguous multi-root case.
        provider = BibleMarkdownProvider.from_env({
            "GAC_BIBLE_PATH": str(bible_path),
            "GAC_EVIDENCE_ROOTS": f"{tmp_path};{tmp_path}",
        })
        assert provider.allowed_roots == [tmp_path, tmp_path]


class TestBibleMarkdownProviderFromEnv:
    def test_missing_env_var_raises(self):
        with pytest.raises(FileNotFoundError):
            BibleMarkdownProvider.from_env({})

    def test_env_var_pointing_at_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            BibleMarkdownProvider.from_env(
                {"GAC_BIBLE_PATH": str(tmp_path / "does-not-exist.md")}
            )

    def test_valid_env_var_constructs_provider(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider.from_env({"GAC_BIBLE_PATH": str(bible_path)})
        assert provider.provider_id == "bible-markdown"
        assert provider.allowed_roots == [bible_path.parent]

    def test_allowed_roots_env_var_is_respected(self, bible_dir):
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider.from_env({
            "GAC_BIBLE_PATH": str(bible_path),
            "GAC_EVIDENCE_ROOTS": str(tmp_path),
        })
        assert Path(tmp_path) in provider.allowed_roots

    def test_configured_env_var_names_are_honored(self, bible_dir):
        """config.yaml's evidence.path_env / evidence.allowed_roots_env name which env
        vars to read; from_env must use those names, not hardcode the default ones."""
        tmp_path, bible_path = bible_dir
        provider = BibleMarkdownProvider.from_env(
            {"CUSTOM_BIBLE_PATH": str(bible_path), "CUSTOM_EVIDENCE_ROOTS": str(tmp_path)},
            path_env_key="CUSTOM_BIBLE_PATH",
            roots_env_key="CUSTOM_EVIDENCE_ROOTS",
        )
        assert provider.bible_path == bible_path
        assert Path(tmp_path) in provider.allowed_roots
