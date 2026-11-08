"""Private access probe: a TEST STAND-IN for a builder workstation on the private network.

Deployed only when enable_private_access_probe = true (agentcore/terraform/probe.tf), as a VPC
Lambda function in the companion's private subnets. It stands in for a workstation connected over
AWS Direct Connect or VPN with private DNS, and checks the internal path end to end:

  1. dns         resolve the gateway host; record the IPs and whether they are private / in the VPC
  2. initialize  POST JSON-RPC `initialize` to the gateway /mcp route
  3. notifications/initialized
  4. tools/list  record whether the companion_kb tool is listed
  5. tools/call  call the companion_kb tool with a fixed test question
  6. runtime     (optional, call_runtime=true) POST to the orchestrator runtime through the
                 AgentCore data-plane endpoint, like the stdio bridge (frontdoor/runtime_client.py)

Diagram mode (opt-in, payload {token, diagram: "<request text>"}): an in-AWS end-to-end diagram
test for private mode when no workstation network path exists. It runs dns, initialize,
notifications/initialized, then `tools/call` of the diagram tool named in GAC_PROBE_DIAGRAM_TOOL_NAME
(fixed in config, never chosen by the caller) with the MCP runtime's draw_diagram arguments
(prompt, environment, title, name, include_artifacts=true). It returns the tool's full result
under `result`: {"response": <gated text>, "diagram": <the gac.diagram/1 artefact envelope the
bridge consumes: drawio, svg, png base64, sha256 and sizes>, "diagram_views": <views 2..n of a
multi-view answer, at most 3, each token-checked like view 1; [] for one view>}. The request text is 1 to 500
characters with no control characters. The tool result is read up to the design's 5 MiB upstream
cap and the whole Lambda result must stay under the 6 MB synchronous response limit; over either,
the probe returns a clear error and no artefacts. The laptop helper
(`./run.sh diagram-fetch --via-probe <function>`) saves the files through
frontdoor/diagram_writer.py, which verifies every sha256 before writing.

Anonymous mode (payload exactly {"anonymous": true}): dns, initialize, notifications/initialized
and tools/list against the gateway with NO Authorization header, never a tools/call and never the
runtime. It returns the usual {ok, steps, ...} document (ok is false on a 401/403), so a deny-first
end-to-end test can prove anonymous denial from inside the VPC. It sends fewer credentials than the
other modes and is invoked only by principals allowed lambda:InvokeFunction. Any other use of the
`anonymous` key is an invalid payload.

The caller passes their own bearer token in the invocation payload. The token is held only in
local variables and the Authorization header of requests to the two allowed hosts. It is NEVER
stored, logged, or returned: every body placed in the result is redacted, logging uses fixed
format strings with no body, header, event or exception text, and an unexpected exception is
reported only by its type name.

The HTTP client never follows redirects, ignores proxy environment variables, verifies TLS
certificates and hostnames, and refuses any host other than the configured gateway host and the
AgentCore data-plane host for the Region, so the token cannot be forwarded elsewhere.

Standard library only. Disable the probe after testing (docs/DEPLOY.md).
"""

import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

_LOG = logging.getLogger("gac.probe")
# The Lambda Python runtime's root logger defaults to WARNING; keep the probe's INFO lines.
_LOG.setLevel(logging.INFO)

QUESTION = "What is the deployment policy?"
MCP_PROTOCOL_VERSION = "2025-06-18"
BODY_LIMIT = 2048
READ_LIMIT = 65536
DEADLINE_MARGIN_MS = 2000
DIAGRAM_STEP = "tools/call:draw_diagram"
DIAGRAM_MAX_CHARS = 500
# The design's upstream body cap (4 MiB payload + 1 MiB, frontdoor/runtime_client.UPSTREAM_CAP).
DIAGRAM_READ_LIMIT = 5 * 1024 * 1024
# Under Lambda's 6 MB synchronous invocation response limit, measured on the ASCII JSON result.
LAMBDA_RESPONSE_CAP = 6_000_000
RESPONSE_TEXT_LIMIT = 16384
# Views 2..n of a multi-view diagram (the engine draws at most 4 views).
MAX_EXTRA_VIEWS = 3

# Per-step request timeouts (seconds). tools/call and runtime reach VPC runtimes that can cold-start.
STEP_TIMEOUTS = {
    "initialize": 10,
    "notifications/initialized": 10,
    "tools/list": 10,
    "tools/call": 45,
    "runtime_invoke": 45,
    # Rendering is bounded at 20 s in the runtime; 90 s also covers a cold start. 10 + 10 + 90
    # plus the margin stays inside the function's 120 s timeout.
    DIAGRAM_STEP: 90,
}

_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[0-9]$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER_RE = re.compile(r"(?i)bearer\s+[^\s\"',;]+")
_INVALID_PAYLOAD = "invalid payload: expected {token, call_runtime?}"
_INVALID_DIAGRAM = "invalid payload: expected {token, diagram} with diagram 1-500 characters"
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class _HostNotAllowed(Exception):
    """Raised before sending when a request targets a non-allowed host or a non-https URL."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never follow; the 3xx surfaces as HTTPError and is recorded (status + redacted body)


def _build_opener(transport=None):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),  # ignore any *_PROXY env
        transport or urllib.request.HTTPSHandler(context=ssl.create_default_context()),  # verify certs and hostnames
        _NoRedirect(),
    )


_OPENER = _build_opener()


def _load_config():
    """Validate the (non-secret) environment once. Returns (config_dict, error_field_or_None)."""
    region = os.environ.get("GAC_PROBE_REGION", "")
    if not _REGION_RE.match(region):
        return None, "GAC_PROBE_REGION"
    gw = urllib.parse.urlsplit(os.environ.get("GAC_PROBE_GATEWAY_URL", ""))
    host_re = r"^[a-z0-9-]+\.gateway\.bedrock-agentcore\." + re.escape(region) + r"\.amazonaws\.com$"
    if (gw.scheme != "https" or not gw.hostname or not re.match(host_re, gw.hostname)
            or gw.port not in (None, 443) or gw.path != "/mcp" or gw.query or gw.fragment
            or gw.username or gw.password):
        return None, "GAC_PROBE_GATEWAY_URL"
    arn = os.environ.get("GAC_PROBE_RUNTIME_ARN", "")
    arn_re = r"^arn:aws[a-z-]*:bedrock-agentcore:" + re.escape(region) + r":\d{12}:runtime/[A-Za-z0-9_-]+$"
    if not re.match(arn_re, arn):
        return None, "GAC_PROBE_RUNTIME_ARN"
    cidr = os.environ.get("GAC_PROBE_VPC_CIDR", "")
    net = None
    if cidr:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return None, "GAC_PROBE_VPC_CIDR"
    tool = os.environ.get("GAC_PROBE_TOOL_NAME", "gac___companion_kb")
    if not re.match(r"^[A-Za-z0-9_-]{1,128}$", tool):
        return None, "GAC_PROBE_TOOL_NAME"
    diagram_tool = os.environ.get("GAC_PROBE_DIAGRAM_TOOL_NAME", "gac___draw_diagram")
    if not re.match(r"^[A-Za-z0-9_-]{1,128}$", diagram_tool):
        return None, "GAC_PROBE_DIAGRAM_TOOL_NAME"
    return {
        "region": region,
        "gateway_host": gw.hostname,
        "gateway_url": "https://" + gw.hostname + "/mcp",
        "dataplane_host": "bedrock-agentcore." + region + ".amazonaws.com",
        "runtime_arn": arn,
        "vpc_net": net,
        "tool": tool,
        "diagram_tool": diagram_tool,
    }, None


_CONFIG, _CONFIG_ERROR = _load_config()
if _CONFIG_ERROR:
    _LOG.warning("config invalid field=%s", _CONFIG_ERROR)
_ALLOWED_HOSTS = frozenset() if _CONFIG is None else frozenset({_CONFIG["gateway_host"], _CONFIG["dataplane_host"]})


def _send(req, timeout):
    parts = urllib.parse.urlsplit(req.full_url)
    if parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS:
        raise _HostNotAllowed()  # recorded as status "host_not_allowed"; nothing is sent
    return _OPENER.open(req, timeout=timeout)


def _redact(text, token):
    """Remove the token, any Bearer value and any JWT-shaped string; truncate to BODY_LIMIT."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if token:
        text = text.replace(token, "[redacted]")
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _JWT_RE.sub("[redacted]", text)
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT] + "...[truncated]"
    return text


def _remaining_ms(context):
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() if callable(getter) else 10 ** 9


def _valid_token(tok):
    return isinstance(tok, str) and 20 <= len(tok) <= 16384 and bool(_TOKEN_RE.match(tok))


def _validate_payload(event):
    """(token, call_runtime) for the probe mode, or None."""
    if not isinstance(event, dict) or not set(event) <= {"token", "call_runtime"}:
        return None
    tok = event.get("token")
    if not _valid_token(tok):
        return None
    call_runtime = event.get("call_runtime", False)
    if not isinstance(call_runtime, bool):
        return None
    return tok, call_runtime


def _validate_diagram_payload(event):
    """(token, request text) for diagram mode, or None. Exactly {token, diagram}; the text is
    1 to DIAGRAM_MAX_CHARS characters after trimming, with no control characters."""
    if not isinstance(event, dict) or set(event) != {"token", "diagram"}:
        return None
    tok = event.get("token")
    if not _valid_token(tok):
        return None
    text = event.get("diagram")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not (1 <= len(text) <= DIAGRAM_MAX_CHARS) or _CTRL_RE.search(text):
        return None
    return tok, text


def _dns(host):
    started = time.monotonic()
    rec = {"step": "dns", "host": host}
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        rec.update(status="dns_error", error=type(e).__name__, ips=[])
        rec["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        _LOG.info("step=%s status=%s elapsed_ms=%d", "dns", "dns_error", rec["elapsed_ms"])
        return rec
    ips = sorted({i[4][0] for i in infos})
    addrs = [ipaddress.ip_address(ip) for ip in ips]
    net = _CONFIG["vpc_net"]
    rec.update(
        status="resolved",
        ips=ips,
        all_private=bool(addrs) and all(a.is_private for a in addrs),
        in_vpc_cidr=(bool(addrs) and all(a in net for a in addrs)) if net is not None else None,
    )
    rec["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    _LOG.info("dns host=%s ips=%s", host, ",".join(ips))
    return rec


def _parse(raw, content_type):
    """JSON body, or the last JSON object in an SSE stream; None when unparseable."""
    if content_type.startswith("text/event-stream"):
        parsed = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    parsed = json.loads(line[5:].strip())
                except ValueError:
                    continue
        return parsed
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _read_capped(resp, limit):
    """Read up to limit + 1 bytes; returns (bytes, over_limit)."""
    buf = bytearray()
    while len(buf) <= limit:
        chunk = resp.read(min(65536, limit + 1 - len(buf)))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf[:limit]), len(buf) > limit


def _http_step(name, url, payload, token, extra, context, read_limit=READ_LIMIT, strict=False,
               keep_body=True):
    """POST JSON and record {step, status, body, elapsed_ms}. Returns (record, parsed, resp_headers).

    token: the caller's bearer token, or None (anonymous mode: no Authorization header; _redact
    then has no token to replace).

    strict: a body over read_limit is not parsed and is recorded as status "too_large".
    keep_body=False: a 2xx body is not copied into the record (the caller returns it parsed)."""
    timeout = STEP_TIMEOUTS[name]
    if _remaining_ms(context) < timeout * 1000 + DEADLINE_MARGIN_MS:
        _LOG.info("step=%s status=%s elapsed_ms=%d", name, "skipped_deadline", 0)
        return {"step": name, "status": "skipped_deadline", "body": "", "elapsed_ms": 0}, None, {}
    hdrs = {"Content-Type": "application/json"}
    if token is not None:  # anonymous mode (token None) sends no Authorization header at all
        hdrs["Authorization"] = "Bearer " + token
    hdrs.update(extra)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
    started = time.monotonic()
    parsed, resp_hdrs = None, {}
    rec = {"step": name}
    try:
        with _send(req, timeout) as resp:
            data, over = _read_capped(resp, read_limit)
            resp_hdrs = {k.lower(): v for k, v in resp.headers.items()}
            if over and strict:
                rec.update(status="too_large", body="")
            else:
                raw = data.decode("utf-8", "replace")
                parsed = _parse(raw, resp_hdrs.get("content-type", ""))
                rec.update(status=resp.status, body=_redact(raw, token) if keep_body else "")
    except _HostNotAllowed:
        rec.update(status="host_not_allowed", body="")
    except urllib.error.HTTPError as e:  # 3xx (never followed), 4xx, 5xx
        try:
            raw = e.read(READ_LIMIT).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the body is best effort
            raw = ""
        rec.update(status=e.code, body=_redact(raw, token))
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", "")
        rec.update(status="network_error", error=type(e).__name__, body=_redact(reason, token))
    rec["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    _LOG.info("step=%s status=%s elapsed_ms=%d", name, rec["status"], rec["elapsed_ms"])
    return rec, parsed, resp_hdrs


def _jsonrpc(method, req_id=None, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def _run(token, call_runtime, context, anonymous=False):
    """Probe mode, or anonymous mode (token None): dns, initialize, notifications/initialized and
    tools/list only, with no Authorization header and never a tools/call or runtime call."""
    cfg = _CONFIG
    steps = []
    result = {"ok": False, "gateway_host": cfg["gateway_host"], "dns": None, "steps": steps,
              "runtime": None, "note": "test stand-in; disable after testing"}
    if anonymous:
        token, call_runtime = None, False
        result["mode"] = "anonymous"
    result["dns"] = _dns(cfg["gateway_host"])

    accept = {"Accept": "application/json, text/event-stream"}
    stopped = False

    def step(name, payload, extra):
        nonlocal stopped
        if stopped:
            steps.append({"step": name, "status": "skipped_deadline", "body": "", "elapsed_ms": 0})
            return None, {}
        rec, parsed, hdrs = _http_step(name, cfg["gateway_url"], payload, token, extra, context)
        if rec["status"] == "skipped_deadline":
            stopped = True
        steps.append(rec)
        return parsed, hdrs

    _, hdrs = step("initialize", _jsonrpc("initialize", 1, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "gac-private-access-probe", "version": "1"},
    }), accept)
    session = {"Mcp-Session-Id": hdrs["mcp-session-id"]} if hdrs.get("mcp-session-id") else {}
    step("notifications/initialized", _jsonrpc("notifications/initialized"), dict(accept, **session))
    listed, _ = step("tools/list", _jsonrpc("tools/list", 2, {}), dict(accept, **session))
    names = []
    if isinstance(listed, dict):
        tools = (listed.get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools if isinstance(t, dict)]
    if steps[-1]["step"] == "tools/list":
        steps[-1]["tool_listed"] = cfg["tool"] in names
    if anonymous:
        result["ok"] = all(isinstance(s["status"], int) and 200 <= s["status"] < 300 for s in steps)
        return result
    step("tools/call", _jsonrpc("tools/call", 3, {"name": cfg["tool"], "arguments": {"query": QUESTION}}),
         dict(accept, **session))

    if call_runtime:
        runtime = {"dns": _dns(cfg["dataplane_host"])}
        url = ("https://" + cfg["dataplane_host"] + "/runtimes/"
               + urllib.parse.quote(cfg["runtime_arn"], safe="") + "/invocations")
        if stopped:
            rec = {"step": "runtime_invoke", "status": "skipped_deadline", "body": "", "elapsed_ms": 0}
        else:
            sid = uuid.uuid4().hex + uuid.uuid4().hex[:8]  # 40 chars; AgentCore wants >= 33
            rec, _, _ = _http_step("runtime_invoke", url, {"prompt": QUESTION, "environment": "dev"}, token,
                                   {"Accept": "application/json",
                                    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid}, context)
        runtime["invoke"] = rec
        result["runtime"] = runtime

    http = [s for s in steps] + ([result["runtime"]["invoke"]] if result["runtime"] else [])
    result["ok"] = all(isinstance(s["status"], int) and 200 <= s["status"] < 300 for s in http)
    return result


def _scrub(text, token):
    """_redact without the 2 KiB body truncation, for the gated response text."""
    if not isinstance(text, str):
        return ""
    if token:
        text = text.replace(token, "[redacted]")
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _JWT_RE.sub("[redacted]", text)
    if len(text) > RESPONSE_TEXT_LIMIT:
        text = text[:RESPONSE_TEXT_LIMIT] + "...[truncated]"
    return text


def _diagram_envelope(parsed, token):
    """(envelope or None, error or None) from the parsed JSON-RPC tools/call reply."""
    if not isinstance(parsed, dict):
        return None, "tools/call reply is not JSON-RPC"
    if isinstance(parsed.get("error"), dict):
        err = parsed["error"]
        return None, _redact("tools/call JSON-RPC error %s: %s" % (err.get("code"), err.get("message", "")), token)
    res = parsed.get("result")
    if not isinstance(res, dict):
        return None, "tools/call reply has no result"
    texts = [c.get("text") for c in res.get("content") or [] if isinstance(c, dict) and isinstance(c.get("text"), str)]
    if sum(len(t.encode("utf-8")) for t in texts) > DIAGRAM_READ_LIMIT:
        return None, "diagram result over the 5 MiB upstream limit; nothing returned"
    joined = "\n".join(texts)
    if res.get("isError"):
        return None, _redact("tool error: " + joined, token)
    try:
        inner = json.loads(joined)
    except ValueError:
        inner = None
    if not isinstance(inner, dict) or not isinstance(inner.get("response"), str):
        return None, "tool result is not a diagram envelope"
    envelope = {"response": _scrub(inner["response"], token)}
    diagram = inner.get("diagram")
    if not isinstance(diagram, dict):
        return envelope, "no artefacts returned (see result.response)"
    views = inner.get("diagram_views", [])
    if not isinstance(views, list) or len(views) > MAX_EXTRA_VIEWS \
            or not all(isinstance(v, dict) for v in views):
        return envelope, "diagram_views is not a list of at most %d views" % MAX_EXTRA_VIEWS
    for part in [diagram] + views:
        packed = json.dumps(part)
        if (token and token in packed) or _JWT_RE.search(packed):
            return envelope, "artefacts withheld: token-shaped content in the result"
    envelope["diagram"] = diagram
    # Always present in this probe version (possibly empty), so the laptop helper can tell an
    # older probe that dropped the extra views from a single-view answer.
    envelope["diagram_views"] = views
    return envelope, None


def _run_diagram(token, request, context):
    cfg = _CONFIG
    steps = []
    result = {"ok": False, "mode": "diagram", "gateway_host": cfg["gateway_host"], "dns": None,
              "steps": steps, "tool": cfg["diagram_tool"], "result": None, "error": None,
              "note": "test stand-in; disable after testing"}
    result["dns"] = _dns(cfg["gateway_host"])
    accept = {"Accept": "application/json, text/event-stream"}
    stopped = False

    def step(name, payload, extra, **kw):
        nonlocal stopped
        if stopped:
            steps.append({"step": name, "status": "skipped_deadline", "body": "", "elapsed_ms": 0})
            return None, {}
        rec, parsed, hdrs = _http_step(name, cfg["gateway_url"], payload, token, extra, context, **kw)
        if rec["status"] == "skipped_deadline":
            stopped = True
        steps.append(rec)
        return parsed, hdrs

    _, hdrs = step("initialize", _jsonrpc("initialize", 1, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "gac-private-access-probe", "version": "1"},
    }), accept)
    session = {"Mcp-Session-Id": hdrs["mcp-session-id"]} if hdrs.get("mcp-session-id") else {}
    step("notifications/initialized", _jsonrpc("notifications/initialized"), dict(accept, **session))
    # The MCP runtime's draw_diagram signature (agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py).
    arguments = {"prompt": request, "environment": "dev", "title": "", "name": "", "include_artifacts": True}
    parsed, _ = step(DIAGRAM_STEP, _jsonrpc("tools/call", 3, {"name": cfg["diagram_tool"], "arguments": arguments}),
                     dict(accept, **session), read_limit=DIAGRAM_READ_LIMIT, strict=True, keep_body=False)
    last = steps[-1]
    http_ok = all(isinstance(s["status"], int) and 200 <= s["status"] < 300 for s in steps)
    if last["status"] == "too_large":
        result["error"] = "diagram result over the 5 MiB upstream limit; nothing returned"
    elif not http_ok:
        result["error"] = "a step did not return HTTP 2xx (see steps)"
    else:
        envelope, error = _diagram_envelope(parsed, token)
        result["result"], result["error"] = envelope, error
        if error and last["step"] == DIAGRAM_STEP:
            last["body"] = ""
    result["ok"] = http_ok and result["error"] is None and isinstance((result["result"] or {}).get("diagram"), dict)
    if len(json.dumps(result)) > LAMBDA_RESPONSE_CAP:
        result.update(ok=False, result=None,
                      error="diagram result over the Lambda 6 MB synchronous response limit; nothing returned")
        _LOG.info("step=%s status=%s elapsed_ms=%d", "result", "too_large", 0)
    return result


def lambda_handler(event, context):
    try:
        if _CONFIG is None:
            return {"ok": False, "error": "probe configuration invalid"}
        if isinstance(event, dict) and "anonymous" in event and "diagram" not in event:
            # Exactly {"anonymous": true}; any other shape is today's invalid payload.
            if set(event) != {"anonymous"} or event["anonymous"] is not True:
                _LOG.info("step=%s status=%s elapsed_ms=%d", "validate", "rejected", 0)
                return {"ok": False, "error": _INVALID_PAYLOAD}
            return _run(None, False, context, anonymous=True)
        if isinstance(event, dict) and "diagram" in event:
            valid = _validate_diagram_payload(event)
            if valid is None:
                _LOG.info("step=%s status=%s elapsed_ms=%d", "validate", "rejected", 0)
                return {"ok": False, "error": _INVALID_DIAGRAM}
            tok, request = valid
            return _run_diagram(tok, request, context)
        valid = _validate_payload(event)
        if valid is None:
            _LOG.info("step=%s status=%s elapsed_ms=%d", "validate", "rejected", 0)
            return {"ok": False, "error": _INVALID_PAYLOAD}
        tok, call_runtime = valid
        return _run(tok, call_runtime, context)
    except Exception as e:  # noqa: BLE001 - never let a traceback (which could hold the token) out
        _LOG.warning("unhandled type=%s", type(e).__name__)
        return {"ok": False, "error": type(e).__name__}
