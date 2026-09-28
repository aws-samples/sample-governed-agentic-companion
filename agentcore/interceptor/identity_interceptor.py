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
    environment-dependent; the runtime reads whichever is present (see runtime_mcp_entrypoint).

SPOOF-PROOFING (the critical security condition, ADR-0001):
  A direct (non-gateway) caller to the runtime never traverses this interceptor, so it cannot
  produce a gateway-injected actor. The runtime therefore trusts the injected actor ONLY on
  the gateway path (the request presents the gateway's M2M principal) and treats a
  client-supplied actor header on any other path as untrusted (ignored). This interceptor
  never trusts a client-supplied `X-Gac-Actor-Sub` on input — it always OVERWRITES it from the
  verified token, so a client cannot pre-seed it.

GOVERNANCE (Tenet 1/3/6): this is a read + request-shaping seam. It performs no deploy or
mutation, writes to no system of record, and logs only the subject *presence* and a short
hash prefix — never the token, never the full subject. Fail-open on parse errors returns the
request UNCHANGED (no actor injected), so a malformed token degrades to the Phase-1
fail-closed refusal rather than to a wrong attribution.
"""

import base64
import hashlib
import json
import logging
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ACTOR_META_KEY = "gac/actor-sub"
_ACTOR_HEADER = "X-Gac-Actor-Sub"
# Mirror the runtime/session-store actor sanitization: AgentCore actorIds / namespace segments
# allow [A-Za-z0-9._-]; replace anything else and bound the length.
_ACTOR_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")


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


def _subject_from_jwt(token: str) -> str:
    """Decode the JWT PAYLOAD (no signature check — the gateway already verified inbound) and
    return the sanitized subject. Returns '' on any parse problem (fail-open to no-actor)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad base64url
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = claims.get("sub") or claims.get("username") or claims.get("client_id") or ""
        return _sanitize_actor(str(sub))
    except Exception as e:  # never raise into the gateway — pass the request through unchanged
        logger.warning("identity-interceptor: could not extract subject (%s); passing through",
                       type(e).__name__)
        return ""


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
    # request headers on an MCP target, the runtime reads X-Gac-Actor-Sub; otherwise it reads
    # params._meta. Belt-and-suspenders across environments.
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "body": body,
                "headers": {_ACTOR_HEADER: actor},
            }
        },
    }
