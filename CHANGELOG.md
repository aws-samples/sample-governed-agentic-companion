# Changelog

All notable changes to the Governed Agentic Companion starter kit are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
semantic versioning (MAJOR.MINOR.PATCH), tracked in `config.yaml` (`system.version`).

- **MAJOR** — breaking changes to the CLI, config schema, or governance model.
- **MINOR** — new capabilities added in a backward-compatible way.
- **PATCH** — fixes and documentation corrections that add/remove no capability.
## [Unreleased]
- **Diagram specialist is a preview.** It is included as a preview and is not yet
  production-ready; a redesign into a collaborating specialist is planned (agent-to-agent
  knowledge gathering and an intent-aware single-view layout). Current limits:
  - No real peer collaboration: it reads only each specialist's hand-authored `diagram_facts`
    slice. It does not consult peer specialists or the orchestrator, or their wider knowledge
    (deployment policy, both network modes, identity and entitlement, front doors, the
    governance gate and tenets, the docs).
  - Intent is largely ignored: the deterministic keyword planner is the default and the Bedrock
    planner is opt-in and off, so words such as "single view", "both" or "public" are not
    understood and are reported as not in the knowledge base.
  - The view splitter overrides an explicit request for a single view and returns up to 4 views.
  - Each view is capped (24 nodes, 6 for a generic view), so a dense, grouped single overview of
    25 to 50 nodes cannot be drawn; the quality check scores legibility, not usefulness or fit
    to the request.
  - Documentation only: no code, image, Terraform or version change.
## [3.4.1] - 2026-11-07 - Browser sign-in for the bridge
PATCH release: the local stdio bridge and `./run.sh diagram-fetch` sign in through the browser by
default (OAuth 2.0 authorization code + PKCE, S256) with the stack's existing public PKCE client
(`cognito_oauth_frontdoor_client_id`), so a developer needs only non-secret values: no client
secret, password or AWS credentials. Front-door helpers only: no image, Terraform or AWS change.
**Upgrade note:** a bridge or `diagram-fetch` environment that still sets
`GAC_COGNITO_CLIENT_SECRET` now fails with "GAC_AUTH_FLOW=pkce uses the public PKCE client and no
client secret"; move to the PKCE values (`docs/FRONT-DOORS.md`, "Bridge sign-in") or set
`GAC_AUTH_FLOW=password` to keep the old flow (test harness and automation only).
- **`GAC_AUTH_FLOW`** = `pkce` (default) or `password`. PKCE env: `GAC_COGNITO_DOMAIN`,
  `GAC_COGNITO_CLIENT_ID` (the public client), `GAC_COGNITO_SCOPES` (exactly `openid` plus the
  `cognito_oauth_scope` value, default `openid gac-engine/invoke`), optional
  `GAC_COGNITO_REDIRECT_PORTS` (default 8080-8084), `GAC_COGNITO_LOGIN_TIMEOUT` (default 180 s)
  and `GAC_COGNITO_NO_BROWSER=1`. Under PKCE set `GAC_GATEWAY_REGION` / `GAC_RUNTIME_REGION`
  explicitly.
- **Sign-in.** The first tool call opens the browser (the URL is also on stderr for headless
  use). A one-shot listener on 127.0.0.1 only serves the single `/callback`, checks `state`,
  returns a fixed page and closes before the code exchange. Tokens stay in memory only and are
  refreshed before expiry; one sign-in per bridge process. No token, code or verifier is logged.
- **Errors.** `Not fetched: Cognito denied the sign-in (...)` for a refused or timed-out sign-in;
  a bad domain, client id, scope, port list or timeout is a config error (`diagram-fetch` exit 2,
  a clean MCP error on the bridge). `--via-probe` always uses the password flow.
- **Bridge.** The runtime-mode `ask_companion` and `companion_kb` tools no longer block the MCP
  event loop. `scripts/e2e_deployed.py` sets `GAC_AUTH_FLOW=password` itself.
- **Samples and docs.** `frontdoor/mcp.example.json` option A is PKCE with non-secret env, plus a
  `kiro_example` pair: Kiro's native OAuth `gac-gateway` (its `gac___draw_diagram` disabled) and
  the `gac-bridge` stdio entry (`GAC_BRIDGE_TOOLS=draw_diagram`) that lands the files. The
  onboarding and plugin bridge samples use PKCE too.
- **Known issue:** the companion's `cognito_hosted_ui_domain` output is valid only when it owns
  the pool; on a shared pool use `https://<platform output shared_user_pool_domain>.auth.<region>.amazoncognito.com`.
- **Security gate:** no new dependency (standard library only); images unchanged. The R2/R4
  residuals carry over unchanged. Two new advisories against the unchanged base, with no fix in a
  released Wolfi build, are accepted residuals approved by the repository owner on 2026-11-06:
  R5 CVE-2026-84243 (`glibc-2.44` 2.44-r11, High) and R6 CVE-2026-116566 (`python-3.15`
  3.15.2_git20261030-r0, Medium). Both also affect the deployed 3.3.1 and 3.4.0 images
  (`docs/security/scan-results.md`).

## [3.4.0] - 2026-11-05 - Natural-language diagrams
MINOR release: the Diagram specialist draws from natural-language requests and questions over the
knowledge base ("draw the architecture end to end", "what talks to Bedrock", "show how a
developer request reaches the knowledge base", "diagram our security boundaries"), splits a
large answer into up to 4 views, and declines honestly, with prompts that work, when the
knowledge base cannot support a request. It is deterministic by default; an opt-in Amazon
Bedrock planner only selects fact ids. Access now also requires the companion's entitlement
group. **Upgrade note:** users already in `<name_prefix>-users` keep access after the apply;
users who are not in it are denied until the pool's owner adds them. On a shared pool the
companion no longer creates the group, and the plan fails clearly if it is missing (create it
through the platform stack's `apps` map or by hand first). Order: install
`requirements-frontdoor.txt` on workstations, make sure the group and its members exist, create
the E2E test users, set a new `image_tag`, plan, review the plan with
`scripts/plan_review.py --forbid-cognito-groups`, apply, then run the deny-first E2E
(`docs/DEPLOY.md`, "Upgrading to 3.4.0").

**UNVERIFIED in AWS:** 3.4.0 is verified locally only (unit tests, mocked `terraform test`, the
security gate, local renders on the CLI and the bridge writer). Not yet run against a deployed
stack: the claim rules (in-place versus replace, the authorizer's denial bodies), the interceptor
key policy, the gateway re-sync script, the gateway's limit on a multi-view tool result, and
the opt-in planner against Bedrock. `scripts/e2e_deployed.py` is the human-run check.

- **Natural-language diagrams.** One fact catalogue per request from every specialist's
  `diagram_facts` (`BaseAgent.diagram_facts(topic=None)` returns all of a specialist's facts),
  a KB-owned vocabulary (`knowledge/tier_a/diagram/vocabulary.yaml`: synonyms, scope words, flow
  order, roles, topology note), a deterministic planner (scopes path, path-in,
  neighbourhood-in/out, security, end-to-end, focus), a shared closure that adds only catalogue
  facts with a reason each, and a view planner (presets first, then flow views). New optional
  fact fields `aliases`, `tags` and `description`; `topics` is now optional (0 to 10). New preset
  `bedrock-calls` and three Bedrock facts; each preset gains a curated `suggestion`. Presets
  keep their names and files ("draw our private network path" still writes
  `private-request-path`).
- **Views.** Up to 4 views per answer (`diagram.max_views`, 1 to 4, tighten-only), each its own
  `gac.diagram/1` bundle named `<base>-<i>-<view id>`, each with its own quality check; more is
  a bounds decline with narrower prompts, never a silent trim.
- **Opt-in Bedrock planner** (`diagram.planner.mode: bedrock`, Terraform `diagram_planner`,
  `diagram_planner_model_id`, `diagram_planner_model_regions`). One Converse call with a forced
  `select_facts` tool, temperature 0, a guardrail, at most 1024 output tokens, no SDK retry
  (`total_max_attempts` 1), about `timeout_seconds` wall time (per-read bound) and a breaker;
  any error falls back to the deterministic planner and the footer names the code. Callers can
  only downgrade (`./run.sh diagram --planner deterministic`, the tool's `planner` argument).
  Plan-time guards require the model id, a guardrail and, in private mode, the bedrock-runtime
  endpoint. New IAM only when enabled: `DiagramPlannerGuardrail`, and
  `DiagramPlannerCrossRegionModels` with Regions listed.
- **Footer.** Diagram answers add `Diagram planner`, `Diagram facts`, `Diagram views` and
  `Diagram source: knowledge base, not the live deployment` lines; every rendered answer says
  "Drawn from the knowledge base, not from the live deployment." plus the topology note.
- **Exit codes (breaking for scripts).** `./run.sh diagram` and `./run.sh diagram-fetch` exit 4
  on an honest decline, on every upstream including `--via-probe`. `diagram-fetch` exits 1 when
  any view was not saved, including an upstream body over the size cap (was 0), and 4 for a
  gate-blocked runtime answer (it has the decline's shape); `./run.sh diagram` still exits 0 on
  a gate block. 0 saved, 2 config, 3 upstream are unchanged.
- **Multi-view payload.** The MCP `draw_diagram` result and `/invocations action: diagram` add
  `diagram_views` (views 2 to n) next to `diagram`; without artefacts, `diagram_manifest.views`.
  The bridge, `diagram-fetch` and the probe save every view; an older client saves view 1 only,
  and an older probe prints "<n> more views were listed but not delivered by this probe
  version". `draw_diagram` gains `planner` and a new description that says it takes questions.
- **Entitlement (action-level authorization).** `require_entitlement_group` (default true;
  refused as false in public mode), `entitlement_group_name` (default `<name_prefix>-users`),
  `manage_entitlement_group` (default follows `manage_identity_pool`; true on a shared pool is
  refused), `check_entitlement_group_exists` (default true; read-only `ListGroups` at plan time)
  and `entitlement_claim_name` (default `cognito:groups`). The gateway and orchestrator
  authorizers gain a CONTAINS claim rule; the orchestrator no longer allows the M2M client; in
  public mode with the gateway the MCP runtime allows only the gateway's M2M client. The identity
  interceptor re-checks the group and fails closed with HTTP 403 "GAC: not entitled".
- **Interceptor environment CMK (resource change).** CKV_AWS_173 is fixed, not suppressed: a
  rotating customer managed key `aws_kms_key.interceptor` with alias
  `alias/<name_prefix>-interceptor`, usable only through Lambda for this function, encrypts the
  interceptor's environment, and its role gains `kms:Decrypt` on that key only (with the same
  ceiling in its permission boundary). Per stack with the interceptor: 3 creates plus in-place
  updates of the function and the boundary.
- **IAM deny now unconditional.** `DenyBoundarySelfModification` on the runtime role applies in
  public mode too (it was private-mode only) and adds `SynchronizeGatewayTargets`,
  `DeleteGateway`, `DeleteGatewayTarget`, `DeleteAgentRuntime`, `DeleteAgentRuntimeEndpoint` and
  `DeleteMemory` (23 actions). A pure tightening: the engine never calls the control plane.
- **Automatic gateway re-sync.** `terraform_data.gateway_tool_sync` runs
  `scripts/sync_gateway_target.sh` inside the human-run apply when the MCP runtime image, version
  or tool surface changes, so the gateway lists `gac___draw_diagram` without a manual step
  (`sync_gateway_tools`, default true; `gateway_sync_settle_seconds`, default 15). An AWS CLI
  without the command fails with "AWS CLI too old"; the manual command is in `docs/DEPLOY.md`.
- **`requirements-frontdoor.txt`.** Exact pins for the front-door helpers (pyyaml 6.0.3, boto3 and
  botocore 1.43.111, mcp 1.30.0, httpx 0.28.1, requests 2.34.2; Python 3.10 or later). A missing
  package gives one line, `<package> is not installed; run: pip install -r
  requirements-frontdoor.txt`, and exit 2, on `diagram-fetch` and the bridge.
- **Tenet 12 view bound.** Work per request rises from at most 3 layouts to at most 15 plus 4
  rasterizations, inside the unchanged 20 s render deadline (it starts after the planner) and one
  4 MiB payload budget across views; a selection is capped at 96 nodes.
- **Probe anonymous mode.** The private access probe accepts exactly `{"anonymous": true}`:
  `initialize` and `tools/list` with no `Authorization` header, never the runtime, so the
  deny-first E2E can prove anonymous denial from inside the VPC.
- **Release checks.** New `scripts/e2e_deployed.py` (human-run, deny-first deployed-stack E2E with
  door and cross-stack parity) and `scripts/plan_review.py` (saved-plan review; exit 1 on a
  protected delete or replace, `--forbid-cognito-groups`). The diagram build trigger now also
  hashes the diagram KB YAML. README gains a version badge (a test pins it to `system.version`).
- **Accepted image residual R4 (also affects 3.3.1 images).** Grype now reports CVE-2026-119586
  (High, CVSS 7.8, local, needs user interaction) in `sqlite-libs` 3.54.1-r0 of the unchanged
  2026-11-03 Chainguard base, so 3.3.1 images built on that base carry it too. The flaw is in the
  SQLite command-line shell's `.recover`; the images ship only the library (no `sqlite3` binary,
  no shell in the runtime stage) and the kit does not import `sqlite3`. No fixed Wolfi package
  exists yet. Approved by the repository owner on 2026-11-05 as R4 in
  `security/accepted-residuals.yaml` (review by 2026-11-30); re-pin the base once a fixed
  `sqlite-libs` ships. No suppression was added or changed.
- **Review fixes (natural-language diagrams).** An arrow that cuts its own end node (for
  example the orchestrator's egress loop and the Cognito JWKS hook) is now routed around it and
  fails the built-in quality check (DQ-P101) if it ever recurs; a south port sits 8 px below the
  caption. The AgentCore grouping is solid and the legend's "dashed box: a grouping only" row is
  gone, so dashed always means opt-in. A request with nothing to anchor on ("draw our
  architecture", "diagram gac", "give me an architecture diagram of the companion") draws the
  architecture end to end, and declines now offer "draw the architecture end to end". Common
  request verbs ("give", "who", "uses", "authenticate") are ignored. "What does X depend on" also
  follows storage reads into X (the runtimes' KB bucket read). "What talks to Bedrock" draws the
  Bedrock Guardrail hop. A multi-view security answer titles each view "Security boundaries:
  ...". The gateway re-sync script waits out a sync already running; the cross-Region model
  grant needs `diagram_planner = "bedrock"`; `diagram-fetch --via-probe` checks boto3 before
  asking for the Cognito password. FRONT-DOORS.md documents that a gate-blocked diagram exits 4
  on `diagram-fetch` and 0 on `./run.sh diagram`.
- **Review fixes, pass 3.** The ApplyGuardrail arrow no longer runs along the guardrail's
  dashed outline and hooks back into it: a fan-out target just below its source moves down
  until one bend into its top face fits, and a run along an arrow's own end node (other than
  its port run) now costs the router and is reported by the built-in check as DQ-L120. The
  router also keeps clear of a group's title icon tile, and the built-in DQ-L118 reports a run
  through one. The guardrail is labelled "Bedrock Guardrails" (alias "guardrails"); the full
  "Amazon Bedrock Guardrails" does not fit a two-line caption and would be truncated.
- **Not built (phase 2):** drawing facts proposed from prose KB entries.

## [3.3.1] - 2026-11-02 - Hardening
PATCH release: both runtime images move to a Chainguard Python base pinned by digest (Trivy: 0
findings at every severity, down from 177 unfixed; Grype: 1 approved Low residual on CPython,
until 2026-11-30), every KICS finding is re-triaged
(0 unsuppressed; each suppression is inline, scoped to its KICS query id or to one attribute
line, and listed with its approval), and a security
gate, `scripts/security_gate.sh`, runs every scanner with pinned versions and fails on any finding
that is not an inline justified suppression or in the approved, dated residual allowlist. No AWS
resource is added or removed and no IAM statement or MCP tool changes; both runtimes update in
place to the new image URI. The Terraform edits are comments only, plus one environment
variable on the private access probe (`GAC_PROBE_DIAGRAM_TOOL_NAME`), which exists only with
`enable_private_access_probe = true` and updates that test stand-in in place.

**UNVERIFIED in AWS:** 3.3.1 is verified locally (both images built, run and scanned on
`linux/arm64`; the bridge e2e against the local containers). The deployed runtimes on the new base
have not run in AWS yet; the post-deploy check is in `docs/DEPLOY.md` ("Kit 3.3.1 is UNVERIFIED in
AWS"). The new CI jobs `security-gate-static` and `kics` have run locally only, not on a CI
runner; the CI gate counts as verified once the first pipeline run is green. The scan results
apply to the images built on 2026-11-03: transitive Python dependencies resolve at build time, so
run the gate (or check ECR scan-on-push) on the build you deploy. The repository owner approved
the then-residuals R1-R3 and the suppressions C1-C12, K1-K10, B1-B3 and D1-D11 on 2026-11-02, and
the narrower K1, K4-K7, K9 and K10 marker forms that replaced the first ones
(`docs/security/scan-results.md`), and on 2026-11-03 the base re-pin, the removal of R1 and R3
and R2 on the new Python version. The gate, in CI too, fails after R2's `review_by` date
(2026-11-30): a fork must re-review it or re-pin the base before then.

**Upgrade (a human runs each step):** (1) stage the AWS Architecture Icons zip as before, or accept
the honest decline; (2) set a new `image_tag`; (3) `terraform plan` and `apply`: expect both image
build `null_resource`s replaced and both runtimes updated in place to the new image URIs, nothing
else (`docs/DEPLOY.md`, "Upgrading from 3.3.0"). No gateway target sync is needed.

- Base image: `cgr.dev/chainguard/python` (`latest-dev` for the build stages, `latest` for the
  runtime), pinned by digest with the tag in a comment; Python 3.15.2, no shell or package manager
  in the runtime stage, uid 65532, `ENTRYPOINT []`, `ttf-dejavu=2.37-r9`. Code, venv and catalog
  are root-owned; only the engine's `.audit`, `.feedback` and `.plans` directories are writable.
  Images shrink from 398-415 MB to 282-299 MB; the shipped diagrams render byte-identical. The
  candidate comparison and the refresh procedure are in `docs/security/scan-results.md` and
  `docs/DEPLOY.md` ("Base image").
- Base re-pin (2026-11-03, approved by the repository owner, 2026-11-03), done in 3.3.1 instead of
  the planned 3.3.2: after the Trivy DB update of 2026-11-03, the first pin (Python 3.15.1) showed
  8 fixable findings (`libexpat1` CVE-2026-113351 and CVE-2026-115506; CVE-2026-115557 and
  CVE-2026-114583 on the three `python-3.15` packages). The 2026-11-03 digests (Python 3.15.2,
  `libexpat1` 2.10.0-r0) fix all eight, and Grype no longer reports CVE-2026-115557 (Critical) or
  CVE-2026-114583 (Medium), so their allowlist entries R1 and R3 are removed.
- One accepted image residual remains (Grype; not reported by Trivy), listed in
  `security/accepted-residuals.yaml` until 2026-11-30: R2 CVE-2026-103995 (Low; no released
  CPython line has the fix), on `python-3.15` 3.15.2_git20261030-r0.
- KICS: the 3.3.0 markers did not apply (each was followed by more comment lines). All 33 findings
  are now suppressed by working markers K1-K10 with justifications. A file-header
  `kics-scan disable=<query id>` (K4-K10) drops only its triaged query, so every other KICS query
  still runs on those files, bucket policies included. An `ignore-line` (K1-K3, the secret-name
  false positives) drops every query on its one line: an attribute or a single ARN value (K3), never a
  resource header or a whole policy document, and no `ignore-block` is used. Shown by injecting unrelated
  misconfigurations into a temporary copy. Two are design choices, approved by the owner: K6 (VPC
  without Network Firewall; the VPC has no internet path, and a team that adds internet egress
  must add network inspection) and K8 (IAM Access Analyzer, an account or Organization control the
  deployer must make sure is enabled; a file-level marker in 13 files, because the query reports
  the file, not a line). A stale `iam.tf` marker
  is removed and the Cognito comments now state the real MFA default (`OPTIONAL`).
- New `scripts/security_gate.sh` (`--require-icons`, `--skip-images`, `--static`): tracked-junk
  check, relative-link check, Checkov, Bandit (all levels), pip-audit, detect-secrets, KICS, local
  image builds, Trivy and Grype on all four images, and the resvg-py SBOM (its version is read from
  the runtime requirements files; a static test asserts both files pin the same one). CI gains the
  jobs `security-gate-static` and `kics` (pinned image).
- `./run.sh diagram-fetch` no longer crashes with a traceback when the upstream refuses (found in
  the sandbox: in private mode, from outside the VPC, the gateway answers HTTP 403 with JSON-RPC
  -32002 and the runtime 403 "explicit deny in a resource-based policy"). Both upstreams now print
  one line, `Not fetched: <gateway|runtime> denied the request (HTTP 403: <short server message>)`,
  plus a private-mode hint, and exit 3 (exit 2 stays for a local config error); another HTTP
  status, an MCP JSON-RPC error, a network failure or a Cognito `NotAuthorizedException` gets its
  own one-line message and exit 3. The message never carries a token, URL or ARN, and nothing is
  written. The IDE bridge tools (`draw_diagram`, `ask_companion`, `companion_kb`) return the same
  text as a clean MCP error result, and `mcp_bridge.py --check-gateway-tools` exits 3 on a
  denial. New `frontdoor/upstream_errors.py`.
- Private access probe, diagram mode (test stand-in; disable after testing): the payload
  `{token, diagram: "<request>"}` (1 to 500 characters) calls `gac___draw_diagram` (fixed in the new
  `GAC_PROBE_DIAGRAM_TOOL_NAME`, never caller-chosen) through the gateway inside the VPC and returns
  the full artefact envelope, bounded by the 5 MiB upstream cap and Lambda's 6 MB response limit.
  New `./run.sh diagram-fetch --via-probe <function> "<request>"` mints the token with
  `frontdoor/cognito_token.py`, invokes the probe and saves the files through the bridge's writer
  (`frontdoor/diagram_writer.py`: sha256 check, traversal refusal, no overwrite), with the quality
  score. This is the private-mode end-to-end diagram test when no workstation network path exists
  (`docs/DEPLOY.md`, 3.3.1 check 4). The probe's role, network, timeout and IAM are unchanged and
  its other modes behave as before. `--via-probe` exits 0 saved, 1 when the local writer refuses
  the files (for example a failed sha256 check; the gateway and runtime paths print the same
  "Not saved" text but exit 0), 2 for a local config error or when combined with `--upstream` or
  a non-default `--environment` (the probe always uses the gateway and `dev`), and 3 for an
  upstream failure. Its Lambda client waits 130 s (past the probe's 120 s timeout) with no
  automatic retries, so one command is one probe run, and a slow render reports `Not fetched:
  probe did not answer in time` rather than a network error.
- The gateway client's error-body hook now reads at most 64 KiB of a chunked (undeclared-length)
  error body; a larger one is dropped and only the status is reported.
- Diagram facts: the gateway endpoint node now cites `modules/private-network/endpoints.tf`, the
  file that creates it, instead of `locals.tf` (3.3.0 follow-up #18, citation part).
- Docs: `docs/DEPLOY.md` no longer says the scan "proves it clean"; `scan-results.md` has
  before/after tables; size-cap and writer wording, the specialist count and the PNG scale are
  corrected; em dashes removed from the MCP sample and bridge.
- **Planned 3.3.2:** a hashed `pip --require-hashes` lock per image (so a rebuild installs the
  scanned transitive versions), and a hashed constraints file for the gate's scan venv (today only
  the four top-level scanners are pinned).
- Known follow-ups, deferred from the 3.3.0 review. Each changes docstrings across many files or
  the rendered diagrams, and is kept out of this patch release so the shipped diagrams stay
  byte-identical and the change stays limited to security:
  - docstrings and tests cite design codes from a design document that does not ship;
  - diagram layout polish: an arrowhead 5 px from a caption, uneven column pitch, a four-bend
    dashed edge, two collinear Cognito edges that read as one hop, two arrowheads close together
    on one gateway face, and a portrait canvas for the private request path; the layout scorer
    lacks a column-pitch check;
  - missing or unlabelled diagram facts: the orchestrator runtime's egress to the runtime ENIs,
    a note on who uses the data-plane endpoint to MCP runtime edge, a JWKS edge from Cognito to
    the MCP runtime, and the label on the gateway endpoint to gateway edge;
  - the default `diagram_manifest` is not in the front-door table or the parity assertions, and
    the 16 KiB budget does not state its allowance for escaping.

## [3.3.0] - 2026-11-02 - Diagram specialist
MINOR release: a Diagram specialist, registered by default, draws an AWS architecture diagram of a
knowledge-base topic and returns `.drawio`, `.svg`, `.png` and a report. Every node and arrow cites
a KB diagram fact or a peer specialist's read-only facts, and the report lists each element's
citation (`elements`); it is LLM-free (verified zone), measures its own layout against the kit's
diagram quality rules (`docs/ARCHITECTURE.md`, "Diagram quality rules"), and passes the always-on
gate like every other answer. The official AWS Architecture Icons are never in the repository:
you stage the zip at image build; without it the images still build and the specialist declines
honestly. No new AWS resource and no IAM change.

**UNVERIFIED in AWS:** 3.3.0 is verified locally only (CLI, and the bridge against the locally
built orchestrator and MCP runtime images). The deployed runtimes, the gateway target sync and the
gateway path have not run in AWS; the post-deploy `draw_diagram` check is in `docs/DEPLOY.md`
("Sandbox validation record").

**Upgrade (a human runs each step, in this order):** (1) stage the AWS Architecture Icons zip in
`agentcore/diagram-icons/` (`docs/DEPLOY.md`, "Stage the AWS Architecture Icons"), or accept the
honest decline; (2) set a new `image_tag` (ECR tags are immutable); (3) `terraform apply` rebuilds
both images and updates the runtimes; (4) run the gateway target sync
(`aws bedrock-agentcore-control synchronize-gateway-targets ...`) and
`python3 frontdoor/mcp_bridge.py --check-gateway-tools`, which expects `gac___draw_diagram`
(`docs/DEPLOY.md`, "Upgrading from 3.2.x"); (5) optionally set `diagram_icons_sha256`.

- New specialist `diagram` (`agents/specialists/diagram_specialist.py`, engine in `agents/diagram/`)
  with example topics `private-request-path` (the default path, no gateway), `gateway-to-runtimes`
  (the opt-in gateway to the MCP runtime) and `kb-ingestion`, and example `diagram_facts` for the
  kit's own private-default topology (`knowledge/tier_a/`): both runtimes in VPC mode with their
  network interfaces in the private subnets, the S3 gateway endpoint on the VPC, opt-in and
  configuration-dependent resources (gateway, its endpoint, the identity interceptor, Cognito)
  drawn with a dashed outline, and the VPN or Direct Connect hop on the builder's arrows. Forks add
  topics and facts in Tier-B (`docs/ARCHITECTURE.md`, "Describe your topology for diagrams").
- Routing: a narrow draw-intent pre-check ("draw ...", "diagram ...", an artefact noun with a
  creation verb, "show our ... path/flow/topology" only when no other specialist scores). Existing
  questions route and answer exactly as before.
- CLI: `./run.sh diagram "<request>" [--title] [--name] [--out] [--overwrite]`,
  `./run.sh diagram --check-facts` (exit 0/1), `./run.sh diagram-fetch "<request>"` (gateway; `--upstream runtime` for the runtime ARN),
  `./run.sh diagram-assets [--zip] [--sha256]` (local catalog in the gitignored `.diagram-assets/`);
  `./run.sh ask` saves the files when it routes to the diagram; `./run.sh status` prints a diagram
  readiness line.
- Runtime `/invocations`: `action: "diagram"` with `title`, `name` and `include_artifacts`; the
  default and `action: "ask"` envelopes are unchanged; other actions get a gated "Unknown action".
- MCP: new tool `draw_diagram` on the MCP runtime (gateway `gac___draw_diagram`) and on the local
  bridge. The bridge verifies each SHA-256 and writes the files under
  `<workspace>/outputs/diagrams/<name>/`; landing files needs the stdio bridge.
- New bridge env: `GAC_BRIDGE_UPSTREAM`, `GAC_BRIDGE_TOOLS`, `GAC_GATEWAY_URL`,
  `GAC_GATEWAY_REGION`, `GAC_GATEWAY_TOOL_PREFIX`, `GAC_DIAGRAM_WORKSPACE_ROOT`,
  `GAC_DIAGRAM_LOCAL_DIR`, `GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE`; `--check-gateway-tools`.
- Plugin: `companion-diagram` sub-agent and the `companion-files` stdio server (gateway upstream,
  `draw_diagram` only); `permissions.example.json` lists `mcp__companion-files` under `ask`.
- Config: `diagram:` block in `config.yaml.example` (enabled, bounds, pass mark; tighten-only).
  Terraform: `diagram_icons_sha256` and `diagram_enabled` (disable-only `GAC_DIAGRAM_ENABLED`).
- Images: a discarded `diagram-assets` build stage builds the sanitized, checksummed icon catalog;
  the final stage adds `resvg-py==0.5.0` and the pinned DejaVu fonts, installs with a pinned
  `pip==26.2.2` and then uninstalls pip (no pip ships in the images); builds use `--pull`. A
  repo-root `.dockerignore` now exists.
- Quality check: crossings (DQ-X101) and long stretched arrows (DQ-L108) are hard findings; the
  whitespace check (DQ-L111) measures each group and the canvas on an 8 px raster; zig-zags are
  judged by shared end points. The SVG keeps its structure (nested groups, titled nodes,
  `connectors`, `arrow-labels`) so it can be measured from the file alone.
- Facts: optional node layout hints `col`, `row` and `align` (never drawn), and `optional` on a
  node (dashed outline). The front doors print saved paths relative to the workspace.
- Docs: README, AGENTS.md, ARCHITECTURE, DEPLOY, FRONT-DOORS, SECURITY, agentcore, frontdoor and
  plugin READMEs, THIRD-PARTY-LICENSES (icon terms, rasterizer licences) and the 3.3.0 scan results.

## [3.2.2] - 2026-11-01 - Release hygiene, security scan fixes, docs
PATCH release: repository hygiene, security-scanner fixes, the constitution wording cleanup, and
documentation that records the 2026-10-31 private-mode sandbox validation. No input is added or
removed, the gate is unchanged, and the private-mode resource policies are unchanged.
**UNVERIFIED in AWS:** the 2026-10-31 sandbox validation covers kit 3.2.1. The 3.2.2 resource
additions below and the rebuilt images have not been applied in AWS; after applying, re-run
post-deploy tests 8, 12 and 16 with the probe re-enabled (`docs/DEPLOY.md`).
**Upgrade:** run `terraform init -upgrade` once (the lock files are no longer committed; `versions.tf`
bounds each provider within v6, without checksum pinning, SD-28) and review the plan, which a human
runs. Expect additions, plus the in-place updates named here:
VPC Flow Logs (KMS key and alias, log group, IAM role and policy, flow log) when the kit creates the
VPC (`create_vpc = true`, or the platform shared network); with `enable_private_access_probe = true`,
a KMS key and alias and an SQS dead-letter queue, plus an in-place update of the probe function and
log group; in `platform/`, three `module.backend` additions (an access-log lifecycle and two
EventBridge notifications) and two in-place updates to the state access-log bucket (see the
delivery fix below). Rebuild and push both runtime images to pick up the patched
OpenTelemetry packages.
- Security: Checkov, Bandit (every level), pip-audit and detect-secrets report zero findings on the
  tracked files. Each remaining suppression is inline with a justification and listed in
  `docs/security/scan-results.md`; the repository-level `agentcore/terraform/.checkov.yaml` is
  removed and its skips are replaced by those inline, justified skips.
- Security: VPC Flow Logs for a VPC the kit creates (365-day log group with its own CMK); the AZ data
  source allowlists the AgentCore-supported zone IDs.
- Security: the private access probe gains an encrypted dead-letter queue, a CMK for its environment
  variables and log group, and 365-day log retention.
- Security: `modules/backend-bootstrap` adds a lifecycle on the state access-log bucket and
  EventBridge notifications on both state buckets. The lifecycle expires access logs 365 days after
  creation and noncurrent versions after 30 days, **including existing logs on an adopted bucket**.
- Fix (resource-changing, platform owners): state-bucket access logs are now deliverable. The
  `state_logs` target bucket moves from SSE-KMS to SSE-S3 default encryption (server access logging
  does not deliver to an SSE-KMS target) and its bucket policy gains the `logging.s3.amazonaws.com`
  `s3:PutObject` grant, scoped to the state bucket and the account. Both are in-place updates of
  existing resources; earlier releases most likely wrote no state access logs. UNVERIFIED in AWS:
  after the human-run apply, check that objects appear under `state-access-logs/`.
- Dependencies: both runtime images move to `aws-opentelemetry-distro` 0.22.0 and
  `opentelemetry-api` 1.45.1 (CVE-2026-114603, CVE-2026-116694). The offline ADOT check passed: the
  0.22.0 wheel still names the MCP instrumentor `aws_mcp`, so private mode still disables it. The
  image build and test 8 are UNVERIFIED in AWS.
- Silent exception handlers in `agents/` and `scripts/generate_bible.py` now log the exception type
  at debug level; no content is logged and no verdict changes.
- Hygiene: `.terraform.lock.hcl` files are untracked and gitignored; providers carry pessimistic
  version constraints (companion `aws ~> 6.66`, platform `aws ~> 6.68`, modules `>= 6.0.0, < 7.0.0`;
  `archive` is now declared). A scratch review file is untracked, and the root `.gitignore` covers
  Terraform state, plans, tfvars, `backend.tf`, build zips, crash logs, tool caches and editor
  directories. Usernames and local paths are removed from tracked files.
- `terraform.tfvars.example`: the `private_client_cidrs` placeholder is `10.0.0.0/16` (was
  `10.0.0.0/8`, the widest value the validation accepts), and the optional private-mode inputs
  (resolver, probe, reused endpoints, `engine_resource_server_identifier`) are listed with their
  defaults. Comments and examples no longer name another companion.
- `PRINCIPLES.md` 1.1.1 (re-baselined; no tenet changed): integrity is verified by `./run.sh status`
  and the unit test suite in CI; the governance footer is described as printed on passing responses,
  with a block notice otherwise; Tenet 13 no longer claims the permission boundary guarantees no
  mutation.
- Docs: the sandbox validation record and testing lessons (`docs/DEPLOY.md`), the validated subset
  in `docs/SECURITY.md`, the Resolver option in `docs/FRONT-DOORS.md`, a roadmap of what is not built
  (repository ingestion, mechanical egress and resource ceilings), adoption paths, the
  `terraform test` clean-copy recipe, and "domain-knowledge companion" in the plugin.
- Docs: architecture diagrams with the official AWS Architecture Icons, replacing the inline
  Mermaid: `docs/architecture/diagrams/private-request-path` (private mode with
  `deploy_gateway = true`: the token from Cognito's public endpoint, the VPN / Direct Connect path,
  the in-process orchestrator, the stdio bridge that is the path without the gateway, and the
  gateway, MCP runtime and orchestrator denials of an outside caller), `runtime-egress` (arrows
  from the caller; flow logs only with `create_vpc = true`) and `platform-adoption-paths` (Path A,
  a consumer of the shared `platform/` stack, then Path B, standalone), each as `.svg`, `.png`, editable `.drawio` and the `.tsx` scene source,
  embedded in `docs/ARCHITECTURE.md` and `README.md`. Forks edit the `.drawio` in diagrams.net.
- Installers: the private-mode hint no longer says the endpoints are "reachable only" from approved
  networks; requests are accepted only through the VPC endpoints, and other networks get HTTP 403.
- Tests: `tests/test_release_consistency.py` checks that the latest `CHANGELOG.md` heading matches
  `system.version` in `config.yaml.example`, that no heading repeats and that no shipped file
  repeats the old installer reachability overclaim; static tests cover the flow
  logs, the probe hardening and the provider constraints.
## [3.2.1] - 2026-10-31 - Private-mode gateway target IAM propagation fix

PATCH release: fixes a race that could leave the gateway target `FAILED` ("Authorization error
when sending message") on a fresh private-mode apply. No input or resource-policy change.
**Upgrade:** no action. A deployment whose target is already `FAILED` (tainted) gets it replaced
on the next apply; expect one new `terraform_data.gateway_invoke_propagation` in private mode.

- The gateway target now depends on `aws_iam_role_policy.gateway_exec_inline` (the gateway role's
  `InvokeAgentRuntime` grant). Before, nothing ordered the target after that grant, so both were
  created in the same second.
- New `terraform_data.gateway_invoke_propagation` (private mode only, zero instances in public
  mode) waits 30 s after the grant and the MCP runtime resource policy, because the target runs a
  single tool sync that does not retry and IAM is eventually consistent. It uses `local-exec`, so
  no new provider is added. It re-runs only when either policy changes and never forces target
  replacement.
- Resource policies are unchanged: the MCP runtime still allows only the gateway execution role
  and denies every other principal; the gateway still denies non-VPC callers.

## [3.2.0] - 2026-10-30 - Shared and optional Terraform modules, private access probe

MINOR release: new backward-compatible Terraform modules, a shared platform stack, and opt-in
inputs. The governance gate keeps no off switch, and Tenets 1, 3 and 6 are unchanged in force.

**Upgrade:** run `terraform init` once (it installs the local `modules/private-network`). Existing
`create_vpc = true` and existing-VPC deployments move their network resources into
`module.private_network[0]` with no replacement (`moved.tf`): expect `has moved to` lines and no
other network change. No input changes are needed for supported shapes. Two tightenings apply only
to deployments that reuse a data-plane endpoint (`existing_interface_endpoint_ids["bedrock-agentcore"]`)
or list `additional_dataplane_vpce_ids`: (1) the Memory resource policy gains one
`DenyOtherPrincipalsOnSharedEndpoint` statement (one resource-policy update), denying principals
other than the companion's runtime role, memory role and knowledge admins; (2) with
`auth_mode = "none"` they now fail plan (G-none-shared): switch to `cognito` or `entraid`, or use a
dedicated network. Shared-network consumers that enable the probe set the new
`existing_client_endpoint_security_group_ids` from the platform output.

Added:
- **Reusable modules** under `agentcore/terraform/modules/`: `backend-bootstrap` (remote state
  bucket, access-log bucket, optional lock table), `shared-identity` (one shared Cognito pool, its
  domain, one entitlement group per companion) and `private-network` (VPC, private subnets,
  interface endpoints, S3 gateway endpoint, endpoint SGs, optional Route 53 Resolver inbound
  endpoint). The first two are companion-neutral ports that keep the original resource addresses,
  so an existing platform state can be adopted (human-run; docs/DEPLOY.md).
- **Platform stack** `agentcore/terraform/platform/` with `terraform.tfvars.example`,
  `backend.tf.example`, a lock at hashicorp/aws 6.68.0, and an optional ONE shared private network
  (`enable_private_network`). Outputs map 1:1 onto companion inputs, plus
  `companion_consumer_inputs`.
- **Companion inputs:** `manage_existing_endpoint_ingress`, `existing_client_endpoint_security_group_ids`,
  `enable_resolver_inbound_endpoint`, `resolver_inbound_client_cidrs`, `enable_private_access_probe`,
  `private_access_probe_reserved_concurrency`, `engine_resource_server_identifier` (default
  `gac-engine`, no change for existing deployments). Outputs `resolver_inbound_ip_addresses`,
  `private_access_probe_function_name`, `private_access_probe_invoke_command`, `cognito_oauth_scope`.
- **Private access probe** (`agentcore/probe/private_access_probe.py`, opt-in, default off): a test
  stand-in for a Direct Connect / VPN workstation that checks DNS, the gateway `/mcp` route and the
  orchestrator runtime with a token passed at invoke time; the token is never stored, logged or
  returned. Disable it after testing.
- **Guards:** G-resolver, G-probe-private, G-probe-gateway, G-probe-client-sg, G-client-sg-subset,
  G-none-shared; warning-only `check "shared_endpoint_extras"`.
- **Tests:** plan-only `terraform test` matrices (`agentcore/terraform/tests/`,
  `agentcore/terraform/platform/tests/`, mocked providers, Terraform 1.7 or later); static tests for
  moved-block coverage, module neutrality and migration equivalence, platform/consumer alignment,
  no internet path, probe IAM and network reach; probe token-handling tests.

Changed:
- Guard 4 no longer requires client CIDRs when every client endpoint is reused.
- docs: adoption paths, consuming the shared network, adopting a platform state, the probe and the
  Resolver endpoint (DEPLOY.md); SD-25, SD-26, SD-27 (SECURITY.md); COST.md and FRONT-DOORS.md.

## [3.1.0] - 2026-10-29 - Agentic AI Lens alignment fixes
MINOR release: new backward-compatible capabilities and documentation corrections. The governance
gate keeps no off switch, and Tenets 1, 3 and 6 are unchanged in force.
**Upgrade:** no Terraform input changes. `grounding.fact_threshold` is now read: it is tighten-only,
so a value below 0.95 is clamped to 0.95 and an invalid value falls back to 0.95. The identity
interceptor source changed in comments only, so the next plan shows an in-place update of the
interceptor Lambda code with no behaviour change. The nightly repository ingestion feature is not
in this release.
Added:
- **Configurable grounding threshold** (`agents/config.py`): the gate, its footer, feedback staging
  and `llm_answer_or_decline` use `grounding.fact_threshold` (default 0.95, configurable upward
  only). Tests in `tests/test_fact_threshold.py`.
- **Guarded feedback staging:** `feedback.record_work` calls `data_protection.scrub_or_reject`
  before it stores a request. A request with secret material is not staged, PII is redacted, and a
  guard failure stages nothing.
- **Golden regression set** (`tests/golden/`): 15 questions over the shipped knowledge base that
  check route, sources, and grounded or declined outcome. A regression check, not an accuracy
  measure.
Changed:
- **Verified-zone answers with sources no longer stage as learning gaps.** The fact bar applies
  only to exploration-zone answers, so the maturity report's open-gap count reflects real gaps.
- **`PRINCIPLES.md` 1.1.0, re-baselined** with the approved change list: integrity verified by
  `./run.sh status` and before every commit; Tenet 2 autonomy clarified (every action stays with a
  human); untrusted input treated as data; promotion refuses Tier A keys; confidence described as
  coded; decline behaviour described. Tenet 8 also states the rule for a source-derived knowledge
  tier. That is a principle only: no source-derived tier or ingestion code ships.
Fixed:
- README and AGENTS.md lead with the domain-knowledge purpose and drop absolute claims; Tier 2/3
  described as fork extension points; success measures listed.
- Data-protection guard, Bedrock Guardrail scope, and interceptor actor use described as built.
- Cost floor and teardown notes: two KMS keys and no Secrets Manager secret.
- Evidence gateway doc pointer, the `mcp==1.30.0` error string, the `network_access` description,
  and the footer described on passing responses only.

## [3.0.0] - 2026-10-28 - Private network mode by default (PrivateLink)

MAJOR release: the Terraform default changes, so an existing deployment fails at plan until it sets
inputs. The governance core is untouched (13 tenets, Tenets 1/3, `PRINCIPLES.md` baseline unchanged).
**Upgrade:** either add the private-mode inputs (`vpc_id`, `private_subnet_ids`,
`private_route_table_ids` or `create_vpc = true`, plus `private_client_cidrs` and
`knowledge_admin_principal_arns`), or keep the previous posture with `network_access = "public"`
and `acknowledge_public_endpoint = true`. For the first private rollout, deploy a fresh sandbox
stack: whether switching an existing `PUBLIC` runtime to `VPC` mode (and the MCP runtime from JWT
to SigV4) updates in place or replaces it is unverified. Every private-mode behaviour is
UNVERIFIED until the post-deploy tests in `docs/DEPLOY.md` are run.

Added:
- **`network_access`** (default `private`). Both runtimes run in `VPC` network mode in your private
  subnets (no NAT, no internet route); interface endpoints with private DNS (ECR, Logs, AgentCore
  data plane and gateway, Bedrock Runtime, X-Ray, Comprehend when used) and an S3 gateway endpoint;
  three security groups with TCP 443 only and no possible `/0` rule; an optional sandbox VPC
  (`create_vpc`, two supported AZs, no IGW/NAT/EIP). Existing-VPC and reuse inputs for shared
  endpoints.
- **AgentCore resource policies:** the gateway and orchestrator deny requests without the approved
  `aws:SourceVpce` (a valid JWT from the internet is refused); the MCP runtime allows only the
  gateway execution role; Memory denies data-plane calls outside the endpoint. Front-door files in
  `generated/` are written only after every policy is attached.
- **SigV4 gateway hop in private mode** (`gateway_iam_role { service = "bedrock-agentcore" }`); the
  gateway role trust narrows to `gateway/<name_prefix>-gateway-*`.
- **ECR pull restriction** (`ecr_pull_restriction`, default `true`): the images contain
  `knowledge/`, so pulls outside the ecr endpoints are denied except for knowledge admins, the
  runtime role and AWS service principals.
- **KB bucket `DenyDataAccessNotViaS3Endpoint`** and **KB key `KnowledgeAdminS3Use`** (private
  mode). Bucket-management actions are never denied; Block Public Access stays on.
- **`knowledge_admin_principal_arns`** (required in private mode) with a narrowness validation
  (no `role/*`), and a `runner_is_knowledge_admin` plan warning.
- **Runtime-role `DenyBoundarySelfModification`** (private mode): explicit Deny on 17
  boundary-changing control-plane actions. `extra_interface_endpoint_services` rejects
  `bedrock-agentcore-control`.
- **Explicit data-plane endpoint action list**; `dataplane_endpoint_extra_actions` for forks.
- **`trace_tool_content`** (default `false`): private mode disables the ADOT `aws_mcp`
  instrumentation, which otherwise writes tool-call arguments into spans.
- `guards.tf` `network_guards` (twelve private/public preconditions), new outputs (endpoint ids and
  DNS names, SG ids, `kb_kms_key_arn`, `private_access_test_command` with no token interpolated).
- **Tests:** `tests/test_mcp_runtime_stateless.py` (in-process MCP runtime: initialize,
  tools/list, an unissued platform `Mcp-Session-Id` accepted, no server-issued session id,
  `stateless_http=True` on `0.0.0.0:8000` at `/mcp`). The in-process MCP runtime tests need
  Python 3.10+ (mcp 1.30.0); on Python 3.9 they are skipped.
  `tests/test_terraform_private_mode_static.py` (python-hcl2 structure checks) and
  `tests/test_frontdoor_runtime_client.py`. New dev/test pins: `mcp==1.30.0` (Python >= 3.10
  marker), `httpx==0.28.1`, `python-hcl2==8.1.4`.
- Docs: private mode across README, ARCHITECTURE, DEPLOY (prerequisites, 15 post-deploy tests),
  SECURITY ("What private mode does not protect", SD-14 to SD-24), FRONT-DOORS and COST.

Changed:
- **Breaking:** `network_access = "public"` with `auth_mode = "none"` is refused (no override).
- **No Cognito M2M client in private mode**; `cognito_m2m_client_id` is empty there.
- **`autoApprove` is now `[]`** in every MCP sample and FRONT-DOORS snippet: each knowledge tool
  call asks for confirmation in the IDE.
- **Stricter `GAC_AGENTCORE_ENDPOINT` handling** in the bridge: a new optional override that
  accepts only the regional or vpce AgentCore host for the configured Region; the URL is built and
  validated before a token is fetched; connection errors name the private-mode cause.
- **Bridge pins:** `mcp==1.30.0`, `boto3==1.43.111`, `requests==2.34.2` (Python 3.10+ for the
  bridge), matching the commented hints in `requirements.txt`.
- `auth_mode = "none"` wording corrected: no JWT authorizer, SigV4 IAM is the default (unverified).
- SD-6 amended; `PROD-READINESS.md` G6 addressed.

## [2.0.1] - 2026-10-10 — Content-security-review remediation (customer-agnostic + supply-chain hygiene)

Patch release. **No capability change and the governance core is untouched** — the 13 tenets,
Tenets 1/3, and the `PRINCIPLES.md` SHA-256 baseline are unchanged. This clears the findings from a
content/code security review (CSR) plus the CI security-dashboard scan. Upgrade is a straight
pull; no config migration.

- **Dependency CVEs (dependency scanning).** Upgraded **PyJWT 2.13.0 → 2.15.1** in both runtime
  requirements files, closing the full PyJWT 2.13.0 CVE family flagged by the scan (CVE-2026-102266
  / -102267 / -102268 / -102271 / -102272 / -102273 — JWK/HMAC authentication-bypass and token-forgery
  issues). Upgraded **mcp (MCP Python SDK) 1.28.0 → 1.30.0** in the MCP runtime, closing the
  session-hijack / cross-session task-access advisories (CVE-2026-52869 / -52870, GHSA-qx49-fqc8-xw99).
  Both requirements files re-verified to resolve cleanly against the `bedrock-agentcore==1.18.1` floor
  on linux/arm64 + Python 3.12.
- **Secret-detection false positive.** The Gitleaks "Password in URL" hit in
  `agents/data_protection.py` is the data-protection scrubber's own password-in-URL **detection**
  regex, not a secret. Suppressed inline with `# gitleaks:allow` (and a `.gitleaks.toml` allowlist);
  the detection regex is byte-for-byte unchanged — the control is suppressed, not weakened.
- **Remediation record.** Added `docs/security/VULN-TRIAGE-2026-10-10.md` and
  `docs/security/VULN-REMEDIATION-2026-10-10.md` dispositioning every scanned row
  (fixed / false-positive / accepted-debt mapped to an SD row).
- **Customer-agnostic hardening (Tenet 6).** Replaced real upstream-engagement identifiers in
  `scripts/leak_scan.sh` (`CUSTOMER_PATTERNS`) with generic placeholder tokens, so the public
  starter kit no longer embeds a real customer identity. A fork now substitutes its own engagement's
  identifiers. The leak-scan guardrail's purpose is unchanged.
- **Supply-chain pinning.** Pinned the CI `pyyaml` install to `6.0.3` (matches `requirements.txt`)
  in the CI configuration, and pinned the `pre-commit` install instruction to `4.6.2` in
  `.pre-commit-config.yaml`, so CI and local hooks resolve reproducible versions.
- **`plugin/.mcp.json` supply-chain note.** Documented the `@latest` risk inline (`_supply_chain_note`)
  with instructions to pin each MCP tool entry to a tested version before use (new SD-13).
- **KMS least-privilege guidance.** Added an inline production-hardening note to the ECR KMS
  key-administration policy in `agentcore/terraform/ecr.tf`, advising a scope-down of the
  service-scoped wildcards (`kms:Delete*`/`kms:Disable*`/`kms:Create*`) for production (new SD-12).
- **Docs.** Added accepted-security-debt rows **SD-12** (KMS wildcards) and **SD-13** (`@latest`)
  to `docs/SECURITY.md`.

## [2.0.0] - 2026-09-25 — Self-evolution loop + governance substrate for an agentic SDLC

Major uplift. **The governance core is unchanged** — the same 13 tenets, Tenets 1/3 still
absolute, and the gate still has **no off switch** (`PRINCIPLES.md` SHA-256 baseline untouched).
This release is MAJOR because it adds new capability surfaces and CLI commands, with an always-on
feedback loop woven into every piece of work. Upgrade: copy the new `config.yaml.example`
(`system.version: 2.0.0`); new runtime data lives under the gitignored `.audit/`, `.feedback/`,
and `.plans/`.

- **Action-level audit trail** (`agents/audit.py`, `./run.sh audit`). Every gated response from
  every front door appends a metadata-only event (front door, agent, trust zone, measured
  confidence, grounding sources, gate decision, findings) to `.audit/trail.jsonl`. No raw text,
  secrets redacted. Observability only — the gate remains the sole enforcement path. Closes the
  action-audit gap behind the say-vs-do concern (knowledge/PRINCIPLES.yaml ENF-004).
- **Feedback / evolution loop** (`agents/feedback.py`, `./run.sh feedback {list|promote|dismiss}`).
  Every work item emits a signal; a real grounding gap stages a *candidate* learning. A human
  reviews and promotes it to a Tier-B overlay entry at a `team` or `project` memory layer. Staged
  → reviewed → promoted (Tenet 8) — never silently absorbed, never self-written; Tier-A stays
  locked (a promotion to a Tier-A key is refused).
- **Layered memory** (`agents/memory_layers.py`). org/team/project precedence over the existing
  Tier-A/Tier-B knowledge base; `project > team > org`; a layer never escalates trust. No-op by
  default (behavior unchanged unless promoted layer entries exist).
- **Staged mode** (`agents/staged.py`, `./run.sh plan {create|show|approve|step}`). Opt-in
  intent → checkboxed plan → **human approval** → execute one gated step at a time. Executing a
  step produces a review-ready artifact and never deploys or mutates anything (Tenet 1); the
  orchestrator stays single-hop (no autonomous agent-to-agent spawning). Plans persist under
  `.plans/` with per-step traceability.
- **Sensors + parity guard** (`agents/sensors.py`, `scripts/parity_check.py`, `./run.sh verify
  [--list|--parity]`). Advisory, inspectable verification manifests (claim-sources,
  required-sections, traceability, no-absolute-claim) that report but never block, plus a
  determinism/parity guard proving every gate front door reaches the same verdict for the same
  input.
- **Governance substrate for an agentic SDLC.** The gate is exposed as a `Stop`-style command
  hook (`integrations/aidlc/`) and a local, deterministic MCP tool
  (`frontdoor/gate_mcp_server.py` → `govern_artifact`), so an agentic SDLC (e.g. AI-DLC) or any
  harness can run an AI-produced artifact through GAC's unbypassable floor before accepting it.
  Fails open when GAC is absent, fails closed on an absolute violation. This is a **technical
  integration, not a partnership or endorsement**, and GAC does not replace an agentic SDLC.
- Suite: 135 tests passing; `./run.sh status` integrity-clean; `./run.sh verify --parity` green.
  MIT-0 preserved.

## [1.1.2] - 2026-09-24 — CSR remediation, confidence calibration, fork-ready LLM peering

- **Content Security Review (Holmes) remediation.** Added non-production/sample disclaimers to
  the README and deployment docs (Cognito/JWT/IAM/OAuth guidance is illustrative, not for
  production as-is); pinned the core dependencies (`pyyaml`, `pytest`) and gave the
  previously-unpinned `aws-opentelemetry-distro` a `>=0.17.1` floor in the AgentCore
  requirements; dropped the bare unpinned `pytest` from the installer. The derived evidence
  corpus (`scripts/generate_bible.py`) now emits **repo-relative** Evidence paths, so a
  generated `BIBLE.md` carries no developer-username PII; the orchestrator's allowed evidence
  roots include the repo root so relative citations resolve.
- **Confidence calibration (relevance damping).** `assess_confidence_detail` now damps the
  grounding score by how much of the request the answer actually addresses, so a response full
  of KB vocabulary that ignores the question can no longer score high on vocabulary alone. An
  on-topic grounded answer still clears the fact bar; a term is matched by stem as well as exact
  substring so paraphrase isn't falsely penalised.
- **Fork-ready LLM peering.** The peering decline is now a reusable `_peering_decline` method,
  and `llm_answer_or_decline(llm_answer=...)` is provided so a fork that adds an LLM tier
  degrades an ungrounded answer to the peering decline instead of a hard gate block — without
  the deterministic base kit taking any LLM dependency (the example specialists remain
  deterministic-only).
- Suite: 105 tests passing; `./run.sh status` integrity-clean. MIT-0 preserved.

## [1.1.1] - 2026-09-23 — Peering decline + lint/dependency hygiene

- **Peering decline (self-evolution).** A specialist that can't ground an answer no longer
  dead-ends. It acknowledges intent and offers grounded paths forward — route via the
  Orchestrator (`ask`), refine within scope, or co-build a genuine new requirement and
  capture the confirmed facts so the knowledge base grows (Tenet 8). Governance intact: no
  fabrication, no action, a human still builds and deploys (Tenet 1).
- **Lint/dependency hygiene.** PowerShell installer: `Write-Host` → an information-stream
  helper (PSAvoidUsingWriteHost) + UTF-8 BOM. Shell installer: `cd … || exit` (SC2164) and a
  read/write-same-file fix (SC2094). MCP runtime floor `mcp>=1.28.0` (CVE-2026-52869/-52870/
  -59950); root advisory guidance refreshed. MIT-0 and publication-compliance preserved.
- Suite: 100 tests passing; `./run.sh status` integrity-clean.

## [1.1.0] - 2026-09-23 — Evidence gateway + multi-IDE front-door parity

- **Optional evidence gateway (`evidence.provider`, default `none`).** Provider-neutral
  evidence-provenance contract (`agents/evidence_gateway.py` + `agents/evidence_providers/`).
  When enabled, a packet is built before the orchestrator dispatches to a specialist, and the
  gate BLOCKS on a missing/tampered/not-READY/route-mismatched packet. GOVERNANCE GUARDRAIL:
  the packet reaches only the gate's `_detect_evidence` detector — never confidence scoring —
  so evidence can only TIGHTEN the gate (Tenet 4 measurement boundary). `./run.sh bible`
  derives `.evidence/BIBLE.md` from `knowledge/` (a pointer into the KB, not a rival ledger —
  Tenet 8); `.evidence/` is gitignored. Byte-for-byte no-op when `provider: none` (the default).
- **Wired front door (was a reference stub).** `frontdoor/runtime_client.py` now invokes the
  deployed AgentCore runtime with a Cognito bearer token (retry-once on 401/403), and
  `frontdoor/mcp_bridge.py` is a working stdio MCP server exposing `ask_companion` /
  `companion_kb`. Governance is unchanged: the bridge relays; the runtime response is gated
  in the cloud.
- **Installable Claude Code plugin (`plugin/`).** Orchestrator + example specialists as
  sub-agents (each `disallowedTools: Bash`), a domain Skill, MCP wiring, `permissions.example.json`,
  and a `Stop`-hook governance backstop that runs the engine's gate over each sub-agent's
  completed response. MIT-0.
- **One-command multi-IDE onboarding (`onboarding/`).** `install-companion-frontdoor.sh` (+
  `.ps1`) sets up the venv, runs `status`, and optionally installs routing pointers for BOTH
  front doors — a Kiro `inclusion: auto` steering pointer and a marked Claude Code `CLAUDE.md`
  block — without overwriting a team's own files. Includes Kiro MCP example configs (local
  bridge + gateway).
- **Docs:** `docs/FRONT-DOORS.md` rewritten to three connection styles (local engine / Claude
  Code plugin / cloud MCP via bridge or gateway); README "What you get" + layout updated; added
  this CHANGELOG and an `AGENTS.md`.
- MIT-0 and the publication-compliance posture preserved. Suite: 100 tests passing;
  `./run.sh status` integrity-clean.

## [1.0.0] - 2026-09-11 — Public starter-kit release

- Initial public release (MIT-0): governed orchestrator + two example specialists (platform,
  security) over a common `BaseAgent`, the always-on Governance Gate (13 tenets, integrity-
  verified), a three-tier execution model (deterministic → Ollama → Bedrock), a hybrid Tier-A/
  Tier-B knowledge base, and the CLI front door (`status` / `ask` / `gate`).
