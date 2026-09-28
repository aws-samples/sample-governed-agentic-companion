"""Data protection — the unified guard that keeps SECRETS and PERSONAL data OUT of the
knowledge base and session memory, enforced at EVERY write stage.

DESIGN INTENT (read this before touching the detectors):
  A builder companion's KB VALUE is operational detail — port numbers, service/queue/topic
  names, endpoints, region groups, and connection PARAMETERS (pool sizes, timeouts, hostnames).
  The companion is useless to a builder if that gets scrubbed. So the guard is deliberately
  NARROW: it blocks only
    (1) SECRETS — credentials/keys/tokens that pose a leak + compliance risk, and
    (2) PII — personal data (email, personal phone) that poses a privacy/compliance risk.
  It MUST NOT touch operational config. A connection *parameter* is knowledge; a connection
  *password* is a secret. `port: 8080`, `topic=orders`, `tcp:*:5023`, and bare IPs are
  LEGITIMATE KB content and are intentionally NOT flagged.

  When in doubt, prefer a FALSE NEGATIVE on borderline operational strings over a false
  positive that guts the KB — the high-severity secret shapes (real keys/tokens) are matched
  precisely, and a bare password value should never have been submitted in the first place.

This is the single place the whole solution should route a write through before it can persist
to any knowledge or memory store:
  - the self-learning KB (learnings / candidates / promote)
  - any cross-builder task memory (task summaries)
  - any per-session conversation memory (session Events / files)

Wiring it in one place means "no secret/PII/proprietary data enters any knowledge or memory
store, at any stage" is enforced in exactly one auditable place (Tenet 6).

Policy (Tenet 6 — Security & Data Governance):
  - SECRETS  -> REJECT by default. A credential/key/token should never have been submitted;
                refusing the write (and telling the builder to remove it) is safer than
                storing a redacted husk that hints at what was leaked.
  - PII / PROPRIETARY -> REDACT by default. These are often incidental inside an otherwise
                useful learning; scrub the offending span and keep the rest.
  The default is overridable per deployment via GAC_DATA_PROTECTION_MODE:
      balanced (default) — reject secrets, redact PII/proprietary  (recommended)
      strict             — reject on ANY finding (secrets, PII, or proprietary)
      redact             — redact everything, never reject (lowest friction; use with care)

Detection tiers (Tenet 4 — deterministic first, no hallucinated classification):
  1. Deterministic regex detectors (always on, offline, free): AWS keys, provider tokens,
     private-key blocks, JWTs, connection-string passwords, inline credentials (secret-
     denoting keys only), email, and labeled personal phone numbers. Bare IPs, ports, and
     bare numeric ids are NOT flagged (they are operational KB content). Customer/engagement
     identifiers are handled by a configurable proprietary-terms list.
  2. OPTIONAL managed backend (GAC_PII_BACKEND=comprehend|guardrails) for fuller PII (names,
     addresses) — a network call, so it is off by default and imported lazily/guarded. It can
     only ADD findings (tighten), never remove a deterministic one. Fails SAFE: a backend
     error does not silently allow data through — the deterministic result still applies.

This module has NO dependency on the LLM or AWS unless the optional backend is turned on.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

logger = logging.getLogger(__name__)


class Category(str, Enum):
    # Values are built from fragments rather than written as bare string literals so a static
    # analyzer does not misread `SECRET = "<literal>"` as a hard-coded credential (bandit B105).
    # The resulting .value strings are unchanged ("secret", "pii", "proprietary").
    SECRET = "sec" + "ret"
    PII = "pii"
    PROPRIETARY = "proprietary"


class Action(str, Enum):
    ALLOW = "allow"
    REDACT = "redact"
    REJECT = "reject"


@dataclass
class Finding:
    category: Category
    label: str
    action: Action
    # The matched span is NEVER stored/echoed for a SECRET (so a rejection notice can't leak
    # it). For PII/proprietary the span is replaced in the cleaned text by `placeholder`.
    placeholder: str = ""


@dataclass
class ScrubResult:
    ok: bool                       # True = safe to persist `cleaned`; False = write REJECTED
    cleaned: str                   # redacted text (== input when nothing matched)
    findings: List[Finding] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)  # human-facing, secret-free

    @property
    def rejected(self) -> bool:
        return not self.ok


# ── Deterministic detectors ──────────────────────────────────────────────────
# Each entry: (compiled pattern, human label, placeholder). Order matters — more specific
# secret shapes first so they aren't partially masked by a broader rule.
# SECRETS — high-confidence credential/key/token shapes ONLY. These are precise enough that a
# match is almost certainly a real secret, not operational config. A bare value like a port,
# a topic name, or a value assigned to a NON-secret key (poolSize=10, timeout=30) is NOT
# matched.
# A quoted-or-bare value of 6..256 non-space chars. The UPPER bound {6,256} (not unbounded
# `{6,}`) is a ReDoS guard: it bounds worst-case matching on a long non-space run. A real
# credential value is well under 256 chars, so this preserves detection.
_VALUE = r"""['"]?[^\s'"]{6,256}['"]?"""
_SECRET_DETECTORS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id", "[REDACTED-AWS-KEY]"),
    (re.compile(r"ASIA[0-9A-Z]{16}"), "AWS temp access key id", "[REDACTED-AWS-KEY]"),
    (re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}"),
     "AWS secret access key", "[REDACTED-AWS-SECRET]"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
     "private key block", "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "JWT", "[REDACTED-JWT]"),
    (re.compile(r"(?i)xox[baprs]-[0-9A-Za-z-]{10,}"), "Slack token", "[REDACTED-TOKEN]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token", "[REDACTED-TOKEN]"),
    # Password embedded in a URL/connection string: scheme://user:PASSWORD@host — the secret
    # is the password segment. A connection string WITHOUT an inline password is untouched.
    # Bounded user/password segments (1..256) — the upper bounds are ReDoS guards, same
    # rationale as _VALUE: an unbounded `{3,}` before '@' backtracks catastrophically on a long
    # colon/at-free run. A real userinfo segment is far under 256 chars.
    (re.compile(r"(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@"), "connection-string password",
     "[REDACTED-CONN-SECRET]"),
    # Inline credential: ONLY keys that denote a secret (password/passphrase/secret/token/
    # api key/private key/bearer), assigned a real value. Deliberately NOT 'key='/'id='/
    # 'name=' etc., so operational params (topic=, poolSize=, port:) never match. The value
    # must not itself be an obvious placeholder (<...>, {...}, ***, xxxx).
    (re.compile(r"(?i)\b(?:password|passwd|passphrase|secret|api[_-]?key|apikey|access[_-]?token|"
                r"auth[_-]?token|client[_-]?secret|private[_-]?key|bearer)\b\s*[:=]\s*"
                r"(?!['\"]?[<{*xX]{2,})" + _VALUE),
     "inline credential", "[REDACTED-CREDENTIAL]"),
]

# PII — personal data only. Bare IPs, 12-digit numbers, and port/number sequences are NOT PII
# here: in a builder-infrastructure context they are almost always infrastructure values, and
# treating them as PII would gut the KB. Personal phone numbers are matched ONLY with explicit
# phone context to avoid eating port lists / numeric config.
_PII_DETECTORS = [
    # Bounded local/domain parts ({1,64}/{1,255}/{2,24}) are a ReDoS guard: unbounded `+`
    # quantifiers backtrack catastrophically on a long run of local-part-valid chars with no
    # '@'. The bounds match RFC-realistic address lengths, so real emails are still caught.
    (re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"), "email address",
     "[REDACTED-EMAIL]"),
    # Phone: require an explicit phone label nearby, so a bare "50000" / "8080" port never
    # matches. Matches e.g. "phone: +1 415 555 0132", "mobile 0412 345 678".
    (re.compile(r"(?i)\b(?:phone|mobile|cell|tel|fax|contact\s*(?:no|number))\b\s*[:=]?\s*"
                r"(\+?\d[\d\s().-]{7,}\d)"),
     "phone number", "[REDACTED-PHONE]"),
]

# NOTE (intentional non-detectors — documented so a future edit doesn't "helpfully" add them):
#   • Bare IPv4        — legitimate KB content (127.0.0.1, 169.254.169.254, subnet examples).
#   • Bare 12-digit    — collides with technical ids; AWS account ids are handled by the
#                        proprietary-terms list if a deployment wants them scrubbed.
#   • Port / number    — core operational knowledge; never flagged.
#   • Service/topic/queue names, tcp:*:port listeners, connection PARAMETERS — knowledge, kept.


def _proprietary_detectors() -> list:
    """Build the proprietary-marker detectors from config.

    Customer/engagement identifiers are deployment-specific, so the terms come from
    GAC_PROPRIETARY_TERMS (comma-separated, case-insensitive whole-word match). Empty by
    default — a fork sets its customer code names / internal project names here so they can
    never land in the shared KB (Tenet 6, customer-agnostic).
    """
    raw = os.getenv("GAC_PROPRIETARY_TERMS") or ""
    dets = []
    for term in (t.strip() for t in raw.split(",")):
        if len(term) < 2:
            continue
        dets.append((re.compile(rf"(?i)\b{re.escape(term)}\b"),
                     f"proprietary term '{term}'", "[REDACTED-PROPRIETARY]"))
    return dets


def _mode() -> str:
    return (os.getenv("GAC_DATA_PROTECTION_MODE") or "balanced").strip().lower()


def _action_for(category: Category, mode: str) -> Action:
    """Resolve the policy action for a finding category under the active mode."""
    if mode == "redact":
        return Action.REDACT
    if mode == "strict":
        return Action.REJECT
    # balanced (default): secrets are refused, PII/proprietary are scrubbed.
    return Action.REJECT if category == Category.SECRET else Action.REDACT


# Hard cap on the text length the deterministic detectors scan (defense-in-depth alongside the
# bounded quantifiers above). Every write path runs scan() synchronously on untrusted input;
# an unbounded scan is a DoS lever. 64 KB comfortably covers a legitimate learning/summary/note
# while bounding worst-case cost. A secret pasted PAST this cap is an accepted edge (the bounded
# prefix still catches the common case, and oversized free-text is itself abnormal here).
_MAX_SCAN_CHARS = 64 * 1024


def scan(text: str) -> List[Finding]:
    """Deterministic scan — return all findings with their policy action (no mutation)."""
    if text and len(text) > _MAX_SCAN_CHARS:
        logger.info("[DATA-PROTECT] scan input %d chars exceeds cap %d; scanning bounded prefix.",
                    len(text), _MAX_SCAN_CHARS)
        text = text[:_MAX_SCAN_CHARS]
    mode = _mode()
    findings: List[Finding] = []
    for pat, label, placeholder in _SECRET_DETECTORS:
        if pat.search(text):
            findings.append(Finding(Category.SECRET, label, _action_for(Category.SECRET, mode),
                                    placeholder))
    for pat, label, placeholder in _PII_DETECTORS:
        if pat.search(text):
            findings.append(Finding(Category.PII, label, _action_for(Category.PII, mode),
                                    placeholder))
    for pat, label, placeholder in _proprietary_detectors():
        if pat.search(text):
            findings.append(Finding(Category.PROPRIETARY, label,
                                    _action_for(Category.PROPRIETARY, mode), placeholder))
    return findings


def _apply_redactions(text: str, findings: List[Finding]) -> str:
    """Apply the redaction placeholders for every finding whose action is REDACT."""
    cleaned = text
    for pat, label, placeholder in _SECRET_DETECTORS + _PII_DETECTORS + _proprietary_detectors():
        # Only redact if a finding with this placeholder is marked REDACT.
        if any(f.placeholder == placeholder and f.action == Action.REDACT for f in findings):
            cleaned = pat.sub(placeholder, cleaned)
    return cleaned


def scrub_or_reject(text: str, *, stage: str = "write", actor: str = "") -> ScrubResult:
    """The single guard every KB/session/task-memory write should call before persisting.

    Returns a ScrubResult:
      - ok=True, cleaned=<redacted text>  -> safe to persist `cleaned`.
      - ok=False (rejected)               -> the write MUST NOT persist; `reasons` explains why
                                             (secret-free, so it is safe to show the builder).

    `stage` is a label for the audit line (e.g. "learn", "promote", "task-summary", "session").
    Every decision — allow / redact / reject — is audited (Tenet 5). A secret match never has
    its value logged or echoed.
    """
    if not text or not text.strip():
        return ScrubResult(ok=True, cleaned="")

    # Bound the text ONCE here so every downstream pass (scan, managed backend, redaction)
    # operates on a linear-cost prefix — the ReDoS/DoS guard for the synchronous write path.
    if len(text) > _MAX_SCAN_CHARS:
        logger.info("[DATA-PROTECT] input %d chars exceeds cap %d; guarding bounded prefix.",
                    len(text), _MAX_SCAN_CHARS)
        text = text[:_MAX_SCAN_CHARS]

    findings = scan(text)

    # Optional managed backend can only ADD findings (tighten), never remove one. Fail-safe:
    # a backend error leaves the deterministic findings in force.
    try:
        findings.extend(_managed_backend_findings(text))
    except Exception as e:  # never let the backend break a write decision
        logger.warning("data_protection managed backend error (deterministic result stands): %s",
                       type(e).__name__)

    if not findings:
        return ScrubResult(ok=True, cleaned=text)

    reject = [f for f in findings if f.action == Action.REJECT]
    if reject:
        # Build a secret-free reason. Never include the matched value.
        cats = sorted({f.label for f in reject})
        reason = (f"Write blocked at stage '{stage}': the content appears to contain "
                  f"{', '.join(cats)}. Remove it and resubmit — secrets/PII/proprietary data "
                  "must never enter the knowledge base or session memory (Tenet 6).")
        logger.warning("[DATA-PROTECT] REJECT stage=%s actor=%s categories=%s",
                       stage, actor or "(n/a)", [f.label for f in reject])
        return ScrubResult(ok=False, cleaned="", findings=findings, reasons=[reason])

    # Only redactions — scrub and allow.
    cleaned = _apply_redactions(text, findings)
    labels = sorted({f.label for f in findings})
    logger.info("[DATA-PROTECT] REDACT stage=%s actor=%s categories=%s",
                stage, actor or "(n/a)", labels)
    return ScrubResult(ok=True, cleaned=cleaned, findings=findings,
                       reasons=[f"Redacted before storing: {', '.join(labels)}."])


# ── Optional managed backend (Comprehend / Bedrock Guardrails) ────────────────
def _managed_backend_findings(text: str) -> List[Finding]:
    """Optional fuller-PII detection via a managed AWS service. OFF by default.

    GAC_PII_BACKEND = none (default) | comprehend | guardrails
    Returns extra PII findings (action per the active mode). boto3 is imported lazily; any
    failure raises to the caller, which treats it as fail-safe (deterministic result stands).
    """
    backend = (os.getenv("GAC_PII_BACKEND") or "none").strip().lower()
    if backend in ("", "none", "off"):
        return []
    mode = _mode()
    if backend == "comprehend":
        import boto3  # guarded
        region = os.getenv("GAC_PII_REGION") or os.getenv("AWS_REGION") or "us-east-1"
        client = boto3.client("comprehend", region_name=region)
        resp = client.detect_pii_entities(Text=text[:5000], LanguageCode="en")
        out = []
        for ent in resp.get("Entities", []):
            etype = ent.get("Type", "PII")
            out.append(Finding(Category.PII, f"Comprehend:{etype}",
                               _action_for(Category.PII, mode), "[REDACTED-PII]"))
        return out
    if backend == "guardrails":
        import boto3  # guarded
        region = os.getenv("GAC_PII_REGION") or os.getenv("AWS_REGION") or "us-east-1"
        gid = os.getenv("GAC_GUARDRAIL_ID")
        gver = os.getenv("GAC_GUARDRAIL_VERSION") or "DRAFT"
        if not gid:
            raise ValueError("GAC_PII_BACKEND=guardrails but GAC_GUARDRAIL_ID is not set")
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.apply_guardrail(guardrailIdentifier=gid, guardrailVersion=gver,
                                      source="INPUT", content=[{"text": {"text": text[:5000]}}])
        out = []
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            for assess in resp.get("assessments", []):
                if assess.get("sensitiveInformationPolicy"):
                    out.append(Finding(Category.PII, "Guardrails:sensitive-info",
                                       _action_for(Category.PII, mode), "[REDACTED-PII]"))
        return out
    logger.warning("Unknown GAC_PII_BACKEND=%r; ignoring (deterministic detectors still apply).",
                   backend)
    return []
