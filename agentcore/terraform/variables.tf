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
  description = "Image tag to build/push and reference from the runtimes (e.g. a git short SHA). Using a mutable tag like 'latest' is discouraged for production."
  type        = string
  default     = "latest"
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
  description = "Name of the S3 bucket for the self-learning KB (prefix kb/) and the curated Tier-B overlay (kb/curated/overlay.json). Leave empty to auto-name as <name_prefix>-kb-<account>-<region>-<suffix>."
  type        = string
  default     = ""
}

variable "kb_prefix" {
  description = "S3 key prefix for the self-learning KB store. The IAM read/write grant is scoped to this prefix."
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
#                             reachable without a token. NEVER use outside an isolated dev
#                             account — a guard below blocks pairing this with a gateway.
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

# ---- Cognito (auth_mode = "cognito") ----------------------------------------------------

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
  description = "OAuth redirect/callback URLs for the IDE PKCE client. Kiro derives the loopback port from the mcp.json `redirectUri` (which MUST end at the port, e.g. http://127.0.0.1:8080 — no path) and appends /oauth/callback itself. Cognito requires EXACT matches, so we register both 127.0.0.1 and localhost at the fixed port. Add Claude's callback here too if needed."
  type        = list(string)
  default = [
    "http://127.0.0.1:8080/oauth/callback",
    "http://localhost:8080/oauth/callback",
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
