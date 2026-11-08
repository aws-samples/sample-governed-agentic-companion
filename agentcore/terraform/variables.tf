# Input variables for the Governed Agentic Companion AgentCore deployment.
#
# Everything account/engagement-specific is a variable with NO committed default that leaks a
# real value. Copy terraform.tfvars.example -> terraform.tfvars (gitignored) and fill it in.
#
# This is a STARTER KIT: fork it per engagement, keep the governance, and adjust these inputs.

variable "aws_region" {
  description = "AWS region to deploy into. Must be a region where Bedrock AgentCore Runtime is GA and the chosen Bedrock model/inference profile is available."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all resource names (lets multiple engagements coexist in one account). Lowercase letters, digits, hyphens."
  type        = string
  default     = "gac"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name_prefix))
    error_message = "name_prefix must be 2-31 chars, start with a letter, and use only lowercase letters, digits, and hyphens."
  }
}

variable "tags" {
  description = "Tags applied to every resource (via the provider default_tags)."
  type        = map(string)
  default = {
    project    = "governed-agentic-companion"
    managed-by = "terraform"
  }
}

# ---- Container images -------------------------------------------------------------------

variable "image_tag" {
  description = "Image tag to build/push and reference from the runtimes (e.g. a git short SHA). Using a mutable tag like 'latest' is discouraged for production. ECR tags are immutable: set a NEW image_tag for every rebuild (for example after staging or replacing the diagram icons zip), or the push fails and the runtimes keep the old image."
  type        = string
  default     = "latest"
}

variable "diagram_icons_sha256" {
  description = "Optional SHA-256 (64 lowercase hex) of the deployer-staged AWS Architecture Icons zip in agentcore/diagram-icons/. When set, the image build fails if the staged zip does not match. Empty = no check (the computed hash is still recorded in the image)."
  type        = string
  default     = ""
  validation {
    condition     = can(regex("^([0-9a-f]{64})?$", var.diagram_icons_sha256))
    error_message = "diagram_icons_sha256 must be empty or 64 lowercase hex characters."
  }
}

variable "diagram_enabled" {
  description = "Diagram specialist on (default). false sets GAC_DIAGRAM_ENABLED=false on both runtimes, so the specialist stays registered and declines."
  type        = bool
  default     = true
}
variable "diagram_planner" {
  description = "Diagram planner: \"deterministic\" (default, no model call) or \"bedrock\" (opt-in: one Converse call selects knowledge-base facts only; needs diagram_planner_model_id and a guardrail, and in private mode the bedrock-runtime endpoint). Sets GAC_DIAGRAM_PLANNER and the planner env on both runtimes only when \"bedrock\"."
  type        = string
  default     = "deterministic"
  validation {
    condition     = contains(["deterministic", "bedrock"], var.diagram_planner)
    error_message = "diagram_planner must be \"deterministic\" or \"bedrock\"."
  }
}
variable "diagram_planner_model_id" {
  description = "Bedrock model or inference-profile id for diagram_planner = \"bedrock\" (the model must support Converse tool use). Empty by default."
  type        = string
  default     = ""
  validation {
    condition     = can(regex("^$|^[a-z0-9.:/-]{3,128}$", var.diagram_planner_model_id))
    error_message = "diagram_planner_model_id must be empty or 3-128 characters of lowercase letters, digits, '.', ':', '/' and '-'."
  }
}
variable "diagram_planner_model_regions" {
  description = "Regions a cross-Region inference profile for the diagram planner may route to. Non-empty, with diagram_planner = \"bedrock\", adds DiagramPlannerCrossRegionModels (bedrock:InvokeModel on those Regions' foundation models, only through an inference profile of this account and Region). Empty (default) adds nothing."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for r in var.diagram_planner_model_regions : can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", r))])
    error_message = "diagram_planner_model_regions entries must be Region names (for example ap-southeast-2)."
  }
}

variable "repo_root" {
  description = "Absolute path to the repo root (the Docker build context — the single-source engine agents/, knowledge/, governance/ is COPYd from here). Defaults to two levels up from agentcore/terraform/."
  type        = string
  default     = ""
}

variable "auto_build_push_images" {
  description = "When true, Terraform runs the docker build+push helper (build_and_push.sh) for both images before creating the runtimes. Set false to build/push out-of-band (CI) and just reference existing tags."
  type        = bool
  default     = true
}

# ---- Bedrock model ----------------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model id / inference-profile id a fork's engine invokes (Tier-3). NOTE: the example engine selects its model via config.yaml (llm.bedrock.model_id), and the runtime IAM grant is region-scoped to foundation-model/* + inference-profile/* (not this id), so this input is a documented convenience for a fork to wire (e.g. into its own runtime env) — it is not consumed by the kit's example engine today. Use an id valid in aws_region (e.g. an 'au.'/'apac.' profile in ap-southeast-2, a 'us.' profile in US regions)."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

# ---- Knowledge base (S3) ----------------------------------------------------------------

variable "kb_bucket_name" {
  description = "Name of the S3 bucket for the KB store prefix (kb/, reserved for a fork's own knowledge tier) and the curated Tier-B overlay (kb/curated/overlay.json). Leave empty to auto-name as <name_prefix>-kb-<account>-<region>-<suffix>."
  type        = string
  default     = ""
}

variable "kb_prefix" {
  description = "S3 key prefix for the KB store, reserved for a fork's own knowledge tier. The IAM read/write grant is scoped to this prefix."
  type        = string
  default     = "kb"
}

variable "kb_overlay_key" {
  description = "S3 object key for the curated Tier-B overlay the engine reads (GAC_KB_OVERLAY_KEY). Must live under kb_prefix so the runtime role can read it."
  type        = string
  default     = "kb/curated/overlay.json"
}

variable "seed_overlay_object" {
  description = "When true, create an empty placeholder overlay object at kb_overlay_key so the first read resolves (a human replaces it via the curated-overlay promotion flow). Does not overwrite an existing object."
  type        = bool
  default     = true
}

# ---- Session / cross-builder memory (AgentCore Memory) — OPT-IN, READY INFRASTRUCTURE ----
#
# A full engine can wire AgentCore Memory as a shared "central brain": per-builder session
# Events (survive restarts) + a summary strategy that powers cross-task recall. THIS starter
# kit's example engine does NOT ship a session/memory seam, so memory is OFF by default here.
# It is left in the stack as READY infrastructure: set enable_memory=true
# and wire your fork's session store to the exported memory_id (GAC_SESSION_STORE /
# GAC_MEMORY_ID in your fork's runtime), so IaC and your engine agree. Provisioning it without
# a consuming engine only creates the (idle) resource — it does not change runtime behaviour.

variable "enable_memory" {
  description = "Provision an AgentCore Memory resource (per-builder session Events + a cross-builder task-summary strategy) as READY infrastructure. Default false: the kit's example engine has no session/memory seam, so this only creates the resource. Set true in a fork whose engine consumes it (wire GAC_SESSION_STORE/GAC_MEMORY_ID yourself)."
  type        = bool
  default     = false
}

variable "memory_sharing_mode" {
  description = "Cross-builder task-memory isolation for a fork that consumes memory: 'shared' (team-wide related-task recall — the central-brain default) or 'isolated' (strict per-builder). Drives the memory-strategy namespace; only meaningful when enable_memory=true."
  type        = string
  default     = "shared"

  validation {
    condition     = contains(["shared", "isolated"], var.memory_sharing_mode)
    error_message = "memory_sharing_mode must be one of: shared, isolated."
  }
}

variable "memory_event_expiry_days" {
  description = "Retention (days) for short-term session Events in AgentCore Memory. Long-term extracted summaries persist independently."
  type        = number
  default     = 30

  validation {
    condition     = var.memory_event_expiry_days >= 7 && var.memory_event_expiry_days <= 365
    error_message = "memory_event_expiry_days must be between 7 and 365."
  }
}

# ---- Data protection (secrets / PII / proprietary — Tenet 6) ----------------------------
#
# A single unified guard (agents/data_protection.py) is available to enforce at every write to
# a knowledge or memory store. It is calibrated to PRESERVE operational config (ports, topics,
# endpoints, connection parameters — the KB's value) and to block only genuine secrets and
# personal data.
#
#   data_protection_mode:
#     balanced (default) — secrets REJECT the write; PII/proprietary are REDACTED (recommended)
#     strict             — reject on ANY finding (secrets, PII, or proprietary)
#     redact             — redact everything, never reject (lowest friction; use with care)
#
#   pii_backend (optional, OFF by default): a managed service for fuller PII (names/addresses).
#     none (default) — deterministic regex detectors only (offline, free)
#     comprehend     — Amazon Comprehend DetectPiiEntities (adds a per-write network call + cost)
#     guardrails     — Bedrock Guardrails ApplyGuardrail (requires guardrail_id)
#   The backend can only TIGHTEN the guard and fails safe (an error never allows data through).

variable "data_protection_mode" {
  description = "Data-protection policy: 'balanced' (reject secrets, redact PII/proprietary — the recommended default), 'strict' (reject on any finding), or 'redact' (redact everything, never reject)."
  type        = string
  default     = "balanced"

  validation {
    condition     = contains(["balanced", "strict", "redact"], var.data_protection_mode)
    error_message = "data_protection_mode must be one of: balanced, strict, redact."
  }
}

variable "pii_backend" {
  description = "Optional managed PII backend for fuller detection (names/addresses): 'none' (default, deterministic only), 'comprehend' (Amazon Comprehend), or 'guardrails' (Bedrock Guardrails, requires guardrail_id). Tighten-only, fail-safe."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "comprehend", "guardrails"], var.pii_backend)
    error_message = "pii_backend must be one of: none, comprehend, guardrails."
  }
}

variable "create_bedrock_guardrail" {
  description = "When true, Terraform CREATES a starter Bedrock Guardrail (PROMPT_ATTACK filter; regex denies for AWS access keys / PEM private keys / JWTs; PII anonymize/block) and wires its id/version to the runtime as GAC_GUARDRAIL_ID/GAC_GUARDRAIL_VERSION. This COMPLEMENTS the deterministic agents/data_protection.py guard (defense in depth), it does not replace it. When true you do NOT need to set guardrail_id. Modeled on the AWS AgentCore reference samples."
  type        = bool
  default     = false
}

variable "guardrail_id" {
  description = "Bedrock Guardrail identifier — used when pii_backend='guardrails' and you are bringing your OWN guardrail. Leave empty and set create_bedrock_guardrail=true to have Terraform create+wire one. The runtime calls ApplyGuardrail with the effective id on each write."
  type        = string
  default     = ""
}

variable "guardrail_version" {
  description = "Bedrock Guardrail version to apply (default DRAFT). Only used when pii_backend='guardrails'."
  type        = string
  default     = "DRAFT"
}

variable "proprietary_terms" {
  description = "Comma-separated engagement identifiers (code names, internal project names) to REDACT from any store, so they never enter the shared KB (customer-agnostic rule). Empty by default; a fork sets its own terms. NOTE: this value is passed as a runtime env var — use code names, not secrets."
  type        = string
  default     = ""
}

# ---- Identity / inbound auth mode -------------------------------------------------------
#
# The inbound JWT authorizer on both the AgentCore Runtime and the Gateway is IdP-agnostic:
# it needs only an OIDC discovery URL + the allowed client ids. That lets us support three
# modes from one switch, without changing the engine or the runtime contract:
#
#   auth_mode = "cognito"  -> Terraform CREATES a Cognito user pool + front-door user client
#                             + an M2M (client_credentials) resource server & client, and
#                             wires the CUSTOM_JWT authorizer to the Cognito discovery URL.
#                             (This is the reference deployment.)
#
#   auth_mode = "entraid"  -> Use an existing Microsoft Entra ID (Azure AD) tenant. Terraform
#                             creates NO IdP resources; you supply the Entra discovery URL and
#                             the app-registration client ids. The CUSTOM_JWT authorizer trusts
#                             Entra-issued tokens. The gateway->runtime OUTBOUND M2M hop uses an
#                             Entra client_credentials app (see gateway.tf / README).
#
#   auth_mode = "none"     -> INTERNAL TEST ONLY. No inbound authorizer, no IdP. The runtime is
#                             reachable without a token. Because the runtimes use
#                             network_mode="PUBLIC", "none" means a PUBLICLY NETWORK-ACCESSIBLE
#                             endpoint with NO authentication — NEVER use it with a public
#                             network mode outside a fully isolated dev account. Guards below
#                             block pairing "none" with a gateway and require the explicit
#                             allow_unauthenticated=true acknowledgement.
#
# In every mode the GovernanceGate is unchanged and absolute (Tenets 1/3/6) — auth_mode only
# controls who may reach the front door, never what the engine is allowed to do.

variable "auth_mode" {
  description = "Inbound identity mode for the runtimes/gateway: 'cognito' (create a Cognito IdP), 'entraid' (use an existing Microsoft Entra ID tenant), or 'none' (unauthenticated — internal test only)."
  type        = string
  default     = "cognito"

  validation {
    condition     = contains(["cognito", "entraid", "none"], var.auth_mode)
    error_message = "auth_mode must be one of: cognito, entraid, none."
  }
}

variable "allow_unauthenticated" {
  description = "Explicit safety interlock for auth_mode='none'. Must be set true to confirm you intend an unauthenticated deployment. Guarded so 'none' cannot be selected by accident."
  type        = bool
  default     = false
}

# ---- Shared identity toggle (commons pattern) -------------------------------------------
#
# The Cognito POOL + users are a SHARED PLATFORM component (one logical instance all flavors
# reference). When true (default) this deployment OWNS the pool (today's behavior). When false
# it CONSUMES the shared pool another stack created, supplying the pool coordinates below; this
# flavor still creates its OWN resource-server scope + clients IN the shared pool (isolation).
# Only ONE deployment per account should own the pool. See platform/README.md for the model.

variable "manage_identity_pool" {
  description = "When true (default), CREATE and own the Cognito pool/domain. When false, CONSUME the shared pool via existing_user_pool_id/existing_oidc_issuer (this flavor still creates its own clients in that pool)."
  type        = bool
  default     = true
}

variable "existing_user_pool_id" {
  description = "Consume-path only (manage_identity_pool=false): the id of the shared Cognito user pool the platform stack created."
  type        = string
  default     = ""
}

variable "existing_oidc_issuer" {
  description = "Consume-path only: the shared pool's OIDC issuer URL (https://cognito-idp.<region>.amazonaws.com/<poolId>)."
  type        = string
  default     = ""
}

variable "existing_frontdoor_client_ids" {
  description = "Consume-path only (rare): extra pre-created shared-pool front-door client id(s) the inbound authorizer should ALSO accept, beyond the ones this flavor creates."
  type        = list(string)
  default     = []
}

variable "existing_m2m_client_id" {
  description = "Consume-path only (rare): an extra pre-created shared-pool M2M client id to add to the runtime inbound allow-list."
  type        = string
  default     = ""
}

variable "existing_oauth_frontdoor_client_id" {
  description = "Consume-path only (rare): an extra pre-created shared-pool PKCE OAuth client id to accept."
  type        = string
  default     = ""
}

# ---- Entitlement group (who may use this companion) -------------------------------------
# The gateway and orchestrator JWT authorizers (and the MCP runtime's, when it has one and the
# gateway does not use OAuth) require the token's group claim to CONTAIN the entitlement group.
# The interceptor re-checks it (defence in depth). See cognito.tf and docs/DEPLOY.md.

variable "require_entitlement_group" {
  description = "Enforce the entitlement group claim (default true). false is refused in public mode; in private mode it is allowed but weaker (any user of the pool with a front-door client token gets in)."
  type        = bool
  default     = true
}

variable "entitlement_group_name" {
  description = "The entitlement group. Empty (default) means \"<name_prefix>-users\"."
  type        = string
  default     = ""
  validation {
    condition     = var.entitlement_group_name == "" || can(regex("^[A-Za-z0-9_-]{1,128}$", var.entitlement_group_name))
    error_message = "entitlement_group_name must be empty or 1-128 characters of letters, digits, '_' and '-'."
  }
}

variable "manage_entitlement_group" {
  description = "Create the entitlement group in the user pool. null (default) follows manage_identity_pool: the own path creates it, the consume path never does (the shared pool's owner creates it). true on the consume path is refused. false on the own path is allowed (groups managed elsewhere: create the group before the first user test)."
  type        = bool
  default     = null
}

variable "check_entitlement_group_exists" {
  description = "Consume path: read the shared pool's groups at plan time (cognito-idp:ListGroups, read-only) and fail when the entitlement group is missing. Set false for a planning identity without ListGroups."
  type        = bool
  default     = true
}

variable "entitlement_claim_name" {
  description = "Token claim that carries the groups. \"cognito:groups\" (default) for Cognito; for auth_mode = \"entraid\" set \"groups\" or \"roles\". Also passed to the identity interceptor."
  type        = string
  default     = "cognito:groups"
  validation {
    condition     = can(regex("^[A-Za-z0-9:_-]{1,64}$", var.entitlement_claim_name))
    error_message = "entitlement_claim_name must be 1-64 characters of letters, digits, ':', '_' and '-'."
  }
}

# ---- Cognito (auth_mode = "cognito") ----------------------------------------------------

variable "engine_resource_server_identifier" {
  description = "Cognito resource-server identifier for this companion; the OAuth scope is <identifier>/invoke. Default \"gac-engine\" (no change for existing deployments). Must be unique within the user pool: a second GAC companion consuming the same shared pool sets its own, for example \"<name_prefix>-engine\"."
  type        = string
  default     = "gac-engine"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}$", var.engine_resource_server_identifier))
    error_message = "engine_resource_server_identifier must be 2-63 chars, start with a letter, and use only lowercase letters, digits, and hyphens."
  }
}

variable "cognito_frontdoor_callback_urls" {
  description = "Optional OAuth callback URLs for the front-door user client (not needed for the ADMIN_USER_PASSWORD_AUTH bridge flow; leave empty for that path)."
  type        = list(string)
  default     = []
}

variable "enable_oauth_frontdoor_client" {
  description = "Create a PKCE PUBLIC Cognito app client for the Kiro/Claude IDE OAuth path (auto-refreshing tokens — no hourly manual re-mint). Requires auth_mode=cognito. Default true."
  type        = bool
  default     = true
}

variable "oauth_frontdoor_callback_urls" {
  description = "OAuth redirect/callback URLs for the IDE PKCE client. Kiro derives the loopback port from the mcp.json `redirectUri` (which MUST end at the port, e.g. http://127.0.0.1:8080 — no path) and appends /oauth/callback itself. Claude Code instead hardcodes the path as /callback (not /oauth/callback) and offers no way to configure it (see https://github.com/anthropics/claude-code/issues/58595 and /issues/66511), so Claude's loopback port needs BOTH suffixes registered. Cognito requires EXACT matches, so we register both 127.0.0.1 and localhost, with both path suffixes, at each port a builder's IDEs may use. Default covers :8080-:8084 so Kiro and Claude Code can each run two companions side by side without a port clash."
  type        = list(string)
  default = [
    "http://127.0.0.1:8080/oauth/callback",
    "http://localhost:8080/oauth/callback",
    "http://127.0.0.1:8081/oauth/callback",
    "http://localhost:8081/oauth/callback",
    "http://127.0.0.1:8082/oauth/callback",
    "http://localhost:8082/oauth/callback",
    "http://127.0.0.1:8083/oauth/callback",
    "http://localhost:8083/oauth/callback",
    "http://127.0.0.1:8084/oauth/callback",
    "http://localhost:8084/oauth/callback",
    # Claude Code hardcodes the callback path as /callback — register it on every port too.
    "http://127.0.0.1:8080/callback",
    "http://localhost:8080/callback",
    "http://127.0.0.1:8081/callback",
    "http://localhost:8081/callback",
    "http://127.0.0.1:8082/callback",
    "http://localhost:8082/callback",
    "http://127.0.0.1:8083/callback",
    "http://localhost:8083/callback",
    "http://127.0.0.1:8084/callback",
    "http://localhost:8084/callback",
  ]
}

variable "cognito_advanced_security_mode" {
  description = "Cognito advanced security posture: 'ENFORCED' (default — adaptive auth + compromised-credential detection, matches the AWS reference samples), 'AUDIT' (log only), or 'OFF'. Applies only to auth_mode=cognito."
  type        = string
  default     = "ENFORCED"

  validation {
    condition     = contains(["ENFORCED", "AUDIT", "OFF"], var.cognito_advanced_security_mode)
    error_message = "cognito_advanced_security_mode must be one of: ENFORCED, AUDIT, OFF."
  }
}

variable "cognito_mfa_mode" {
  description = "Cognito MFA policy: 'OPTIONAL' (default — TOTP MFA is available so an MFA guardrail is defined, without forcing it on a dev front door), 'ON' (required — recommended for production), or 'OFF' (no MFA; only for an isolated throwaway dev pool). Applies only to auth_mode=cognito."
  type        = string
  default     = "OPTIONAL"

  validation {
    condition     = contains(["OFF", "OPTIONAL", "ON"], var.cognito_mfa_mode)
    error_message = "cognito_mfa_mode must be one of: OFF, OPTIONAL, ON."
  }
}

# ---- Microsoft Entra ID (auth_mode = "entraid") -----------------------------------------
#
# Terraform does not create the Entra tenant/app registrations (that is an Azure-side, human
# task). Supply the values from your app registrations. The INBOUND authorizer needs the
# discovery URL + the front-door client id(s); the OUTBOUND gateway->runtime M2M hop needs a
# client-credentials app (its secret is stored in Secrets Manager, never in tfvars).

variable "entraid_discovery_url" {
  description = "Entra ID OIDC discovery URL, e.g. https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration (v2.0). Required when auth_mode='entraid'."
  type        = string
  default     = ""
}

variable "entraid_frontdoor_client_ids" {
  description = "Entra app-registration client id(s) (the 'aud'/appid the front door presents) allowed by the inbound JWT authorizer. Required when auth_mode='entraid'."
  type        = list(string)
  default     = []
}

variable "entraid_m2m_client_id" {
  description = "Entra client-credentials app id for the gateway->runtime OUTBOUND M2M hop (optional; only if you deploy the gateway with an Entra outbound credential). The secret is read from Secrets Manager at deploy time, not stored here."
  type        = string
  default     = ""
}

# ---- Gateway (opt-in) -------------------------------------------------------------------
#
# The AgentCore Gateway + outbound OAuth M2M credential provider + gateway target are NATIVE
# Terraform resources (hashicorp/aws v6.66+: aws_bedrockagentcore_gateway,
# _oauth2_credential_provider, _gateway_target) — see gateway.tf. They are created only when
# deploy_gateway=true and auth_mode is cognito|entraid (guards.tf refuses auth_mode="none").

variable "deploy_gateway" {
  description = "When true, create the AgentCore Gateway + outbound M2M credential provider + gateway target (native Terraform resources) fronting the mcp_runtime. Requires auth_mode=cognito or entraid (guarded)."
  type        = bool
  default     = false
}

variable "gateway_exception_level" {
  description = "Gateway error verbosity. \"DEBUG\" makes the gateway return the REAL upstream/forwarding error instead of the sanitized 'An internal error occurred' — use ONLY while diagnosing a broken tools/call hop (the DEBUG body leaks ARNs/role names). Leave EMPTY (\"\") for the production default, where client-facing errors are redacted. The API accepts only \"DEBUG\" or unset — there is no \"PRODUCTION\" literal."
  type        = string
  default     = ""
  validation {
    condition     = contains(["DEBUG", ""], var.gateway_exception_level)
    error_message = "gateway_exception_level must be \"DEBUG\" or \"\" (empty = production default; there is no PRODUCTION literal)."
  }
}

variable "enable_identity_interceptor" {
  description = "When true (and deploy_gateway=true), attach the gateway REQUEST interceptor Lambda that propagates the VERIFIED builder subject to the runtime (ADR-0001 Phase 2), turning per-builder memory from fail-closed to functional on the gateway path. Set false to keep the Phase-1 fail-closed behavior."
  type        = bool
  default     = true
}

variable "sync_gateway_tools" {
  description = "Re-sync the gateway target's tool list inside the apply when the MCP runtime image, its version or its tool surface changes (terraform_data.gateway_tool_sync; needs an AWS CLI with bedrock-agentcore-control synchronize-gateway-targets). Set false to run the manual command in docs/DEPLOY.md after apply."
  type        = bool
  default     = true
}
variable "gateway_sync_settle_seconds" {
  description = "Seconds to wait before the tool re-sync, so a new MCP runtime version settles behind the DEFAULT endpoint. A heuristic, not a documented AWS figure."
  type        = number
  default     = 15
  validation {
    condition     = var.gateway_sync_settle_seconds >= 0 && var.gateway_sync_settle_seconds <= 120 && floor(var.gateway_sync_settle_seconds) == var.gateway_sync_settle_seconds
    error_message = "gateway_sync_settle_seconds must be a whole number from 0 to 120."
  }
}
variable "interceptor_reserved_concurrency" {
  description = "Reserved concurrent executions for the identity interceptor Lambda (checkov CKV_AWS_115). Caps the interceptor's share of account Lambda concurrency so a spike cannot starve other functions; the gateway invokes it once per request, so a small reservation is sufficient. Set -1 to use unreserved (account default)."
  type        = number
  default     = 10
}

variable "ecr_immutable_tags" {
  description = "When true (default), ECR repositories use IMMUTABLE image tags (checkov CKV_AWS_51) — a pushed tag cannot be overwritten, so a deployed digest is reproducible. Compatible with the versioned-tag deploy pattern (each release bumps the tag). Set false only if you must overwrite a tag in place."
  type        = bool
  default     = true
}

variable "enable_access_analyzer" {
  description = "When true, create an account-level IAM Access Analyzer (kics 'IAM Access Analyzer Not Enabled'). OFF by default because Access Analyzer is an account/org baseline control — enabling it here would collide with a landing-zone-managed analyzer and duplicate across forks. Set true only when this stack owns the account baseline (e.g. an isolated sandbox)."
  type        = bool
  default     = false
}

variable "gateway_mcp_supported_versions" {
  description = "MCP protocol versions the gateway negotiates with the mcp_runtime target. The gateway's live tools/call hop has been observed to request 2025-03-26 while the runtime's mcp SDK also advertises 2025-06-18. Include BOTH so the gateway accepts whichever the hop uses — pinning to only one makes the gateway reject the other ('Unsupported protocol version'). Set [] to let the gateway choose its newest (risks a future version whose session contract differs)."
  type        = list(string)
  default     = ["2025-06-18", "2025-03-26"]
}

variable "gateway_m2m_secret_arn" {
  description = "BYO OVERRIDE only (optional). The gateway's outbound M2M hop does NOT need a hand-managed Secrets Manager secret — it uses the native AgentCore Identity OAuth2 credential provider (gateway.tf). Set this ONLY if a fork wants to reference an EXISTING secret it manages elsewhere (with its own rotation policy) for a non-gateway path, e.g. an Entra client-credentials secret. Leave empty (default) for the standard cognito deployment."
  type        = string
  default     = ""
}

# NOTE: the kit intentionally does NOT create a hand-managed Secrets Manager copy of the M2M
# credential (and therefore has no manage_m2m_secret / m2m_secret_rotation_days /
# m2m_rotation_lambda_arn knobs). The gateway authenticates via the AgentCore Identity OAuth2
# credential provider, which manages the credential in its own Token Vault — avoiding a
# redundant long-lived secret and its rotation obligation. A fork needing a readable copy on a
# non-gateway path supplies gateway_m2m_secret_arn above and manages that secret's rotation.

# ---- Front-door config generation -------------------------------------------------------

variable "generate_frontdoor_config" {
  description = "When true, Terraform writes populated MCP front-door config files (Kiro + Claude) and a front-door env template into ./generated/ from the deployment outputs, so builders can start using the solution."
  type        = bool
  default     = true
}

# ---- Network access (private by default; public is a DEMO opt-in) -----------------------
#
# network_access = "private" (DEFAULT): both runtimes run in VPC mode in your private subnets
# (no internet route), and the gateway, the runtimes, the KB bucket, AgentCore Memory and the
# ECR images accept data requests only through your VPC endpoints (AWS PrivateLink) or a named
# knowledge-admin principal. A valid JWT presented from the internet is refused by an explicit
# resource-policy Deny (network.tf, resource_policies.tf, guards.tf).
#
# network_access = "public": DEMO ONLY. Today's behaviour: the knowledge plane is reachable
# from the internet and protected only by the inbound JWT authorizer. Requires the second
# interlock acknowledge_public_endpoint = true. auth_mode = "none" is refused in public mode.

variable "network_access" {
  description = "Network exposure of the knowledge plane. 'private' (DEFAULT, recommended): both runtimes run in VPC mode in your private subnets with no internet route, and the gateway, runtimes, KB bucket and Memory accept requests only through your VPC endpoints (AWS PrivateLink); a valid JWT from the internet is designed to be refused (confirm with post-deploy tests 2 and 4a). 'public': DEMO ONLY. WARNING: the gateway and runtime endpoints are reachable from the internet and protected only by the inbound JWT authorizer; anyone holding a valid token can query your proprietary knowledge from anywhere. Requires acknowledge_public_endpoint = true."
  type        = string
  default     = "private"

  validation {
    condition     = contains(["private", "public"], var.network_access)
    error_message = "network_access must be one of: private, public."
  }
}

variable "acknowledge_public_endpoint" {
  description = "Explicit interlock for network_access='public'. Set true only for a demo in a sandbox account, to confirm you accept an internet-reachable knowledge plane protected only by the JWT authorizer. Ignored in private mode."
  type        = bool
  default     = false
}

variable "create_vpc" {
  description = "Private mode only. When true, create a minimal private VPC for a one-apply SANDBOX: two private subnets in AgentCore-supported AZs, one route table with only the local route and the S3 gateway endpoint, no internet gateway, no NAT, DNS hostnames/support on. For any real deployment use your own VPC (create_vpc=false + vpc_id/private_subnet_ids/private_route_table_ids)."
  type        = bool
  default     = false
}

variable "vpc_cidr" {
  description = "CIDR of the sandbox VPC when create_vpc=true. Two /20 private subnets are carved from it."
  type        = string
  default     = "10.80.0.0/16"

  validation {
    # IPv4 only (cidrnetmask rejects IPv6). Every parse sits inside can()/try() because HCL
    # does not short-circuit && / ||.
    condition     = can(cidrnetmask(var.vpc_cidr)) && can(tonumber(split("/", var.vpc_cidr)[1])) && try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    error_message = "vpc_cidr must be an IPv4 CIDR with a prefix between /16 and /24."
  }
}

variable "private_subnet_az_ids" {
  description = "create_vpc only. AZ IDs (for example use1-az1) for the two sandbox subnets. Empty = the first two available AZ IDs that AgentCore Runtime supports in aws_region."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for z in var.private_subnet_az_ids : can(regex("^[a-z0-9]+-az[0-9]+$", z))])
    error_message = "private_subnet_az_ids entries must be AZ IDs such as use1-az1."
  }
}

variable "vpc_id" {
  description = "Existing VPC for private mode (create_vpc=false). Must have DNS hostnames and DNS resolution enabled (private DNS for endpoints). Prefer a VPC dedicated to the companion: the private-DNS endpoints and the S3 gateway endpoint policy the kit creates affect every workload in the VPC (see existing_interface_endpoint_ids)."
  type        = string
  default     = ""

  validation {
    condition     = var.vpc_id == "" || can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must be empty or a VPC id (vpc-...)."
  }
}

variable "private_subnet_ids" {
  description = "Existing private subnets (>= 2, different supported AZs, no route to an internet gateway or NAT recommended) for the runtime ENIs and the interface endpoints."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.private_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "private_subnet_ids entries must be subnet ids (subnet-...)."
  }
}

variable "private_route_table_ids" {
  description = "Route tables that serve private_subnet_ids (checked at plan time), for the S3 gateway endpoint. WARNING: a gateway endpoint changes S3 routing and applies its restrictive endpoint policy to EVERY subnet associated with these route tables, including other workloads; use route tables dedicated to the companion subnets, or supply existing_s3_gateway_endpoint_id."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for r in var.private_route_table_ids : can(regex("^rtb-[0-9a-f]{8,17}$", r))])
    error_message = "private_route_table_ids entries must be route table ids (rtb-...)."
  }
}

variable "private_client_cidrs" {
  description = "IPv4 source CIDRs of the developer/IDE networks (VPN client pool, on-premises ranges over Site-to-Site VPN or Direct Connect, in-VPC desktops) allowed to reach ONLY the AgentCore gateway and data-plane endpoints on TCP 443. Never 0.0.0.0/0; prefix /8 or longer. Required in private mode unless create_vpc=true (then defaults to vpc_cidr)."
  type        = list(string)
  default     = []

  validation {
    # IPv4 only (the SG rule uses cidr_ipv4; IPv6 such as ::/0 fails cidrnetmask). 0.0.0.0/0,
    # ::/0 and anything wider than /8 are rejected.
    condition     = alltrue([for c in var.private_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "private_client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}

variable "bedrock_runtime_client_access" {
  description = "Private mode only. Also allow private_client_cidrs to reach the bedrock-runtime interface endpoint, for IDE assistants configured to use Amazon Bedrock in this account (for example Claude Code with Bedrock, docs/SECURITY.md 'What private mode does not protect'). Off by default: clients need only the gateway and data-plane endpoints."
  type        = bool
  default     = false
}

variable "existing_s3_gateway_endpoint_id" {
  description = "Reuse an S3 gateway endpoint already attached to private_route_table_ids instead of creating one (a route table cannot carry two S3 gateway endpoints). Its policy must allow the ECR layer bucket and the KB bucket."
  type        = string
  default     = ""

  validation {
    condition     = var.existing_s3_gateway_endpoint_id == "" || can(regex("^vpce-[0-9a-f]{8,17}$", var.existing_s3_gateway_endpoint_id))
    error_message = "existing_s3_gateway_endpoint_id must be empty or a VPC endpoint id (vpce-...)."
  }
}

variable "existing_interface_endpoint_ids" {
  description = "Reuse interface endpoints you already run with private DNS (only one endpoint per service per VPC can own the private DNS name). Keys: bedrock-agentcore, bedrock-agentcore.gateway, bedrock-runtime, ecr.api, ecr.dkr, logs, xray, comprehend. The kit does not create those; you must allow TCP 443 from the runtime security group on them. bedrock-agentcore-control is deliberately not an accepted key, so it cannot be wired to the runtimes through this map. SHARED-VPC WARNING: endpoints the kit creates with private DNS take over the regional service name for EVERY workload in the VPC, and the kit's endpoint policies allow only this account's AgentCore resources (bedrock-agentcore) and this account's principals (bedrock-runtime, comprehend). Other workloads in the VPC that call other accounts' AgentCore resources, or principals from other accounts in RAM-shared subnets, are then denied. Use a dedicated companion VPC, or reuse your central endpoints here."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k in keys(var.existing_interface_endpoint_ids) : contains(["bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "xray", "comprehend"], k)])
    error_message = "existing_interface_endpoint_ids keys must be one of: bedrock-agentcore, bedrock-agentcore.gateway, bedrock-runtime, ecr.api, ecr.dkr, logs, xray, comprehend."
  }

  validation {
    condition     = alltrue([for v in values(var.existing_interface_endpoint_ids) : can(regex("^vpce-[0-9a-f]{8,17}$", v))])
    error_message = "existing_interface_endpoint_ids values must be VPC endpoint ids (vpce-...)."
  }
}

variable "existing_endpoint_security_group_ids" {
  description = "Security groups of reused interface endpoints; the runtime SG gets HTTPS egress to every member of them. They must not front a bedrock-agentcore-control endpoint (or any endpoint the runtimes do not need): the runtime SG can reach everything behind these groups. If your central endpoints share one SG, create a dedicated SG for the reused endpoints the companion needs. Private mode also denies boundary-changing control-plane actions to the runtime role (DenyBoundarySelfModification), so this is defence in depth, not the only control."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.existing_endpoint_security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", s))])
    error_message = "existing_endpoint_security_group_ids entries must be security group ids (sg-...)."
  }
}

variable "existing_client_endpoint_security_group_ids" {
  description = "The subset of existing_endpoint_security_group_ids that fronts the reused bedrock-agentcore and bedrock-agentcore.gateway endpoints. Used only by the private access probe, so the probe never gets a rule on a runtime-only endpoint SG. Every entry must also be in existing_endpoint_security_group_ids (guards.tf). From the platform output of the same name."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.existing_client_endpoint_security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", s))])
    error_message = "existing_client_endpoint_security_group_ids entries must be security group ids (sg-...)."
  }
}

variable "additional_dataplane_vpce_ids" {
  description = "Extra AgentCore data-plane endpoint IDs (for example in a shared hub VPC) allowed to invoke the orchestrator runtime and Memory."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for v in var.additional_dataplane_vpce_ids : can(regex("^vpce-[0-9a-f]{8,17}$", v))])
    error_message = "additional_dataplane_vpce_ids entries must be VPC endpoint ids (vpce-...)."
  }
}

variable "additional_gateway_vpce_ids" {
  description = "Extra AgentCore Gateway endpoint IDs allowed to invoke the gateway."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for v in var.additional_gateway_vpce_ids : can(regex("^vpce-[0-9a-f]{8,17}$", v))])
    error_message = "additional_gateway_vpce_ids entries must be VPC endpoint ids (vpce-...)."
  }
}

variable "enable_bedrock_runtime_endpoint" {
  description = "Create the bedrock-runtime interface endpoint (model reasoning and Bedrock Guardrails stay on PrivateLink). Disable only if no Bedrock call is made."
  type        = bool
  default     = true
}

variable "enable_xray_endpoint" {
  description = "Create the X-Ray interface endpoint used by the ADOT tracer in both images."
  type        = bool
  default     = true
}

variable "extra_interface_endpoint_services" {
  description = "Extra interface endpoints by short service name (for example sts, monitoring) if post-deploy tests show a service the runtimes need. Default policy (full access) applies. bedrock-agentcore-control is rejected (docs/SECURITY.md SD-21)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.extra_interface_endpoint_services : can(regex("^[a-z0-9.-]+$", s))])
    error_message = "extra_interface_endpoint_services entries must be short service names such as sts or monitoring."
  }

  validation {
    condition     = alltrue([for s in var.extra_interface_endpoint_services : !contains(["bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "xray", "comprehend"], s)])
    error_message = "extra_interface_endpoint_services must not repeat an endpoint the kit already manages (bedrock-agentcore, bedrock-agentcore.gateway, bedrock-runtime, ecr.api, ecr.dkr, logs, xray, comprehend)."
  }

  validation {
    # Fail closed on the control plane (substring match also catches the full service name and
    # any -fips variant).
    condition     = alltrue([for s in var.extra_interface_endpoint_services : !can(regex("bedrock-agentcore-control", s))])
    error_message = "bedrock-agentcore-control must not be reachable from the companion runtimes: it would let runtime code change the resource policies and network mode that form the private boundary (docs/SECURITY.md SD-21)."
  }
}

variable "ecr_pull_restriction" {
  description = "Private mode: deny ECR image pulls that do not come through the ecr endpoints or an exempt principal. Disable only if post-deploy test 13b fails; then any account principal with ECR pull rights can pull the knowledge-bearing images from the internet."
  type        = bool
  default     = true
}

variable "dataplane_endpoint_extra_actions" {
  description = "Private mode only. Extra bedrock-agentcore actions to allow through the kit's AgentCore data-plane endpoint, beyond the default list (InvokeAgentRuntime, the workload-token and credential-provider token actions, and the Memory data-plane actions when enable_memory=true). Add one only when post-deploy test 8 shows an AccessDenied that names the VPC endpoint policy."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for a in var.dataplane_endpoint_extra_actions : can(regex("^bedrock-agentcore:[A-Za-z]+$", a))])
    error_message = "dataplane_endpoint_extra_actions entries must be single bedrock-agentcore:<Action> names (no wildcards, no other service)."
  }
}

variable "knowledge_admin_principal_arns" {
  description = "Private mode, REQUIRED (non-empty). Every IAM role/user that reads or writes knowledge from OUTSIDE the VPC: each principal that runs terraform (refresh reads the KB bucket and the seed object), each CI role that builds and pushes images, and each curator who promotes the Tier-B overlay. These principals are exempt from the KB bucket and ECR repository VPC-endpoint-only denies; nobody else in the account can read the bucket or pull the images from the internet. The current terraform runner is auto-exempted as a safety net, with a plan warning if it is not listed."
  type        = list(string)
  default     = []

  validation {
    # IAM role/user ARN; * and ? globs allowed (evaluated with ArnLike/ArnNotLike), for example
    # an SSO permission-set role arn:aws:iam::<account-id>:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_<set>_*.
    condition     = alltrue([for a in var.knowledge_admin_principal_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/*?-]+$", a))])
    error_message = "knowledge_admin_principal_arns entries must be IAM role or user ARNs (arn:aws:iam::<account-id>:role/<name> or :user/<name>); * and ? globs are allowed."
  }

  validation {
    condition     = alltrue([for a in var.knowledge_admin_principal_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/[^*?]{3,}", a))])
    error_message = "A knowledge-admin ARN must name a specific role/user or a narrow pattern, not role/* (at least 3 literal characters after role/ or user/ before any wildcard)."
  }
}

variable "trace_tool_content" {
  description = "Private mode only. When false (default), the ADOT MCP instrumentation (aws_mcp) is disabled so tool-call arguments and results are not written to X-Ray/CloudWatch spans, which are readable through IAM from any network. Set true only if you accept tool content in traces and restrict trace read access."
  type        = bool
  default     = false
}

# ---- Shared network and on-premises DNS (private mode) ------------------------------------

variable "manage_existing_endpoint_ingress" {
  description = "Private mode, existing VPC. When true, add an ingress rule (TCP 443 from this companion's runtime SG) to every existing_endpoint_security_group_ids entry. Set true when consuming the platform stack's shared network; needs ec2:AuthorizeSecurityGroupIngress on those SGs. Default false leaves operator-managed central endpoint SGs untouched (you add the rule yourself)."
  type        = bool
  default     = false
}

variable "enable_resolver_inbound_endpoint" {
  description = "Private mode only. Create a Route 53 Resolver inbound endpoint so DNS forwarders on Direct Connect / VPN networks can resolve the endpoints' private DNS names (output resolver_inbound_ip_addresses). Off by default. It costs per IP-hour and per query (docs/COST.md), and it answers ANY DNS name the VPC can resolve for the approved CIDRs (docs/SECURITY.md SD-26). Its security group cannot be changed after creation."
  type        = bool
  default     = false
}

variable "resolver_inbound_client_cidrs" {
  description = "IPv4 CIDRs of the on-premises DNS forwarders allowed to query the Resolver inbound endpoint (TCP/UDP 53). Empty = the effective private_client_cidrs. Never an all-addresses CIDR; prefix /8 or longer."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.resolver_inbound_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "resolver_inbound_client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}

# ---- Private access probe (TEST STAND-IN, opt-in) -----------------------------------------

variable "enable_private_access_probe" {
  description = "Deploy the private access probe: a small Python Lambda function in the private subnets that stands in for a builder workstation on Direct Connect / VPN, and checks DNS, the gateway /mcp route and (optionally) the orchestrator runtime with a token you pass at invoke time (never stored, logged or returned). Private mode with deploy_gateway=true only. A TEST STAND-IN: disable it after testing (docs/DEPLOY.md)."
  type        = bool
  default     = false
}

variable "private_access_probe_reserved_concurrency" {
  description = "Reserved concurrent executions for the probe (default 1). -1 = unreserved (use when the account's unreserved concurrency would drop below the 100-unit minimum)."
  type        = number
  default     = 1

  validation {
    condition     = var.private_access_probe_reserved_concurrency == -1 || (var.private_access_probe_reserved_concurrency >= 1 && var.private_access_probe_reserved_concurrency <= 5)
    error_message = "private_access_probe_reserved_concurrency must be -1 (unreserved) or between 1 and 5."
  }
}
