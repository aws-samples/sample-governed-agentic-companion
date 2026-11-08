"""AgentCore Gateway REQUEST interceptor — verified caller-identity propagation (ADR-0001 Phase 2).

WHAT THIS CLOSES (the WAF Agentic AI Lens AGENTSEC02-BP01 gap):
  Through the gateway, the builder's JWT is validated at the gateway's inbound edge but is NOT
  forwarded to the runtime (the gateway calls the runtime with its own M2M identity). So the
  runtime cannot key per-builder memory on the real builder, and ADR-0001 Phase 1 makes those
  actions FAIL CLOSED (refuse) rather than pool under a shared identity. THIS interceptor is
  the wiring that turns them back ON, safely: it derives the builder's identity from the
  ALREADY-VERIFIED inbound token and injects it into the request the gateway forwards to the
  runtime, so the runtime can attribute per-builder memory to a real, verified actor.

HOW (verified against the AWS interceptor contract, gateway-interceptors-types.html):
  - Configured as a REQUEST interceptor on the MCP gateway with `passRequestHeaders=true`, so
    the input payload includes `mcp.gatewayRequest.headers`, which carries the builder's
    `Authorization: Bearer <jwt>` (the gateway validated it inbound before invoking us).
  - We decode the JWT payload and extract the subject (`sub`; falling back to `username`/
    `client_id`). We do NOT re-verify the signature here: the gateway is the trust boundary
    that already verified issuer/signature/expiry/allowed-client on the inbound edge, and this
    Lambda is only reachable via that gateway (its resource policy allows only the gateway
    service principal to invoke it). Extraction, not verification, is this seam's job.
  - We inject the sanitized subject into the outbound MCP JSON-RPC request as
    `params._meta["gac/actor-sub"]` AND (best-effort) as a request header `X-Gac-Actor-Sub`.
    Two channels because the exact channel the gateway forwards to an MCP target is
    environment-dependent; a fork's runtime reads whichever is present. The example engine
    does not read it yet.

SPOOF-PROOFING (the critical security condition, ADR-0001):
  A direct (non-gateway) caller to the runtime never traverses this interceptor, so it cannot
  produce a gateway-injected actor. A fork's runtime therefore trusts the injected actor ONLY
  on the gateway path (the request arrives from the gateway) and treats a client-supplied
  actor header on any other path as untrusted (ignored). This interceptor
  never trusts a client-supplied `X-Gac-Actor-Sub` on input — it always OVERWRITES it from the
  verified token, so a client cannot pre-seed it.

GOVERNANCE (Tenet 1/3/6): this is a read + request-shaping seam. It performs no deploy or
mutation, writes to no system of record, and logs only the subject *presence* and a short
hash prefix — never the token, never the full subject. Fail-open on parse errors returns the
request UNCHANGED (no actor injected), so a malformed token degrades to the Phase-1
fail-closed refusal rather than to a wrong attribution.

ENTITLEMENT (D26.3, defence in depth): when GAC_REQUIRED_GROUP is set, the request is forwarded
only if the token's group claim (GAC_REQUIRED_GROUP_CLAIM, default cognito:groups) contains the
group; otherwise the interceptor answers 403 "GAC: not entitled" itself (fail closed).
"""

import base64
import hashlib
import json
import logging
import os
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ACTOR_META_KEY = "gac/actor-sub"
_ACTOR_HEADER = "X-Gac-Actor-Sub"
# Mirror the runtime/session-store actor sanitization: AgentCore actorIds / namespace segments
# allow [A-Za-z0-9._-]; replace anything else and bound the length.
_ACTOR_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")

# ---- Entitlement defence in depth (D26.3) -------------------------------------------------
# With GAC_REQUIRED_GROUP set (Terraform sets it when the entitlement group is enforced), a
# request is forwarded only when the claim named by GAC_REQUIRED_GROUP_CLAIM (default
# cognito:groups; groups or roles for Entra ID) is a list of strings that contains the group.
# Anything else fails closed with a 403. The gateway's JWT authorizer is the primary gate; this
# re-check is defence in depth. Unset GAC_REQUIRED_GROUP keeps the behaviour above unchanged.
_CLAIM_NAME_RE = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
_MAX_GROUPS = 100
_MAX_GROUP_LEN = 128
_NOT_ENTITLED_CODE = -32001
_NOT_ENTITLED_MESSAGE = "GAC: not entitled"

REQUIRED_GROUP = os.environ.get("GAC_REQUIRED_GROUP", "")
GROUP_CLAIM = os.environ.get("GAC_REQUIRED_GROUP_CLAIM", "cognito:groups")
# fullmatch so a trailing newline is not accepted.
GROUP_CLAIM_VALID = bool(_CLAIM_NAME_RE.fullmatch(GROUP_CLAIM))
if REQUIRED_GROUP and not GROUP_CLAIM_VALID:
    # Once, at cold start. Every call is then refused (fail closed).
    logger.error("identity-interceptor: GAC_REQUIRED_GROUP_CLAIM is invalid; every request is refused")


def _sanitize_actor(sub: str) -> str:
    sub = (sub or "").strip()
    if not sub:
        return ""
    return _ACTOR_SANITIZE.sub("-", sub)[:120]


def _bearer_from_headers(headers: dict) -> str:
    """Case-insensitive Authorization bearer extraction."""
    if not isinstance(headers, dict):
        return ""
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() == "authorization" and isinstance(v, str):
            parts = v.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
    return ""


def _claims_from_jwt(token: str) -> dict:
    """Decode the JWT PAYLOAD (no signature check — the gateway already verified inbound).
    Raises on any parse problem; the payload must be a JSON object."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # pad base64url
    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    if not isinstance(claims, dict):
        raise ValueError("claims are not an object")
    return claims


def _subject_from_jwt(token: str) -> str:
    """Return the sanitized subject of the JWT payload. Returns '' on any parse problem
    (fail-open to no-actor)."""
    try:
        claims = _claims_from_jwt(token)
        sub = claims.get("sub") or claims.get("username") or claims.get("client_id") or ""
        return _sanitize_actor(str(sub))
    except Exception as e:  # never raise into the gateway — pass the request through unchanged
        logger.warning("identity-interceptor: could not extract subject (%s); passing through",
                       type(e).__name__)
        return ""


def _entitled(token: str) -> bool:
    """True only when the configured group claim is a bounded list of strings containing the
    required group. Every other case (no token, unparseable token, missing or malformed claim,
    invalid claim name) is False: fail closed."""
    if not token or not GROUP_CLAIM_VALID:
        return False
    try:
        claims = _claims_from_jwt(token)
    except Exception:
        return False
    groups = claims.get(GROUP_CLAIM)
    if not isinstance(groups, list) or len(groups) > _MAX_GROUPS:
        return False
    if not all(isinstance(g, str) and len(g) <= _MAX_GROUP_LEN for g in groups):
        return False
    return REQUIRED_GROUP in groups


def _not_entitled(body) -> dict:
    """The pinned 403 response; the gateway returns it to the caller immediately."""
    rid = body.get("id") if isinstance(body, dict) else None
    if isinstance(rid, bool) or not isinstance(rid, (str, int)):
        rid = None
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 403,
                "body": {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": _NOT_ENTITLED_CODE, "message": _NOT_ENTITLED_MESSAGE},
                },
            }
        },
    }


def _passthrough(body: dict) -> dict:
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": body}}}


def lambda_handler(event, context):
    """REQUEST interceptor entry point. Injects the verified builder subject into the outbound
    MCP request; passes through unchanged when no subject can be derived (Phase-1 fail-closed
    then applies at the runtime)."""
    mcp = event.get("mcp", {}) or {}
    gw_req = mcp.get("gatewayRequest", {}) or {}
    body = gw_req.get("body", {}) or {}
    headers = gw_req.get("headers", {}) or {}

    token = _bearer_from_headers(headers)

    if REQUIRED_GROUP:
        if not _entitled(token):
            logger.info("identity-interceptor: not entitled")
            return _not_entitled(body)
        logger.info("identity-interceptor: entitled")

    actor = _subject_from_jwt(token) if token else ""

    if not actor:
        # No verified subject → do NOT invent one. Pass through; the runtime's Phase-1 guard
        # will refuse per-builder actions rather than mis-attribute (ADR-0001).
        logger.info("identity-interceptor: no verified subject; passing request through")
        return _passthrough(body)

    # Inject into the JSON-RPC body's params._meta (MCP's standard out-of-band metadata slot).
    # We OVERWRITE any client-supplied value so a caller cannot pre-seed the actor.
    if isinstance(body, dict):
        params = body.setdefault("params", {}) if isinstance(body.get("params", {}), dict) else {}
        if isinstance(params, dict):
            meta = params.setdefault("_meta", {}) if isinstance(params.get("_meta", {}), dict) else {}
            if isinstance(meta, dict):
                meta[_ACTOR_META_KEY] = actor
                params["_meta"] = meta
                body["params"] = params

    # Log only presence + a short non-reversible hash prefix (never the subject/token).
    digest = hashlib.sha256(actor.encode("utf-8")).hexdigest()[:8]
    logger.info("identity-interceptor: injected verified actor (sha256[:8]=%s)", digest)

    # Return the transformed body AND the actor header. If the gateway forwards allowlisted
    # request headers on an MCP target, a fork's runtime reads X-Gac-Actor-Sub; otherwise it
    # reads params._meta. The example engine does not read it yet.
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": body,
                "headers": {_ACTOR_HEADER: actor},
            }
        },
    }
