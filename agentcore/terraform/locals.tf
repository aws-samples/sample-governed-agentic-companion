# Derived values + safety interlocks. Centralizes the auth-mode resolution so every resource
# (runtimes, gateway, generated config) reads a single consistent set of locals.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Repo root (Docker build context). Default: two levels up from agentcore/terraform/.
  repo_root = var.repo_root != "" ? var.repo_root : abspath("${path.module}/../..")

  # KB bucket name: explicit, or a globally-unique auto-name.
  kb_bucket_name = var.kb_bucket_name != "" ? var.kb_bucket_name : "${var.name_prefix}-kb-${local.account_id}-${local.region}-${random_string.suffix.result}"

  # ---- Auth mode resolution -------------------------------------------------------------
  auth_is_cognito = var.auth_mode == "cognito"
  auth_is_entraid = var.auth_mode == "entraid"
  auth_is_none    = var.auth_mode == "none"

  # An inbound JWT authorizer is attached for cognito + entraid; never for none.
  inbound_jwt_enabled = local.auth_is_cognito || local.auth_is_entraid

  # JWT issuer the inbound authorizer validates.
  # cognito: derived from the created pool; entraid: derived from the supplied discovery URL.
  jwt_issuer = local.auth_is_cognito ? (
    "https://cognito-idp.${local.region}.amazonaws.com/${one(aws_cognito_user_pool.this[*].id)}"
    ) : (
    local.auth_is_entraid ? replace(var.entraid_discovery_url, "/.well-known/openid-configuration", "") : ""
  )

  # Discovery URL feeding the CUSTOM_JWT authorizer on the runtimes/gateway.
  jwt_discovery_url = local.auth_is_cognito ? (
    "${local.jwt_issuer}/.well-known/openid-configuration"
    ) : (
    local.auth_is_entraid ? var.entraid_discovery_url : ""
  )

  # Allowed front-door client ids (the GATEWAY's inbound allow-list — the humans/front doors
  # that may call the gateway). Includes BOTH the static-header front-door client AND the PKCE
  # OAuth client, since either may present a token to the gateway.
  jwt_allowed_clients = local.auth_is_cognito ? distinct(concat(
    aws_cognito_user_pool_client.frontdoor[*].id,
    var.enable_oauth_frontdoor_client ? aws_cognito_user_pool_client.oauth_frontdoor[*].id : [],
    )) : (
    local.auth_is_entraid ? var.entraid_frontdoor_client_ids : []
  )

  # The RUNTIME's inbound allow-list must ALSO include the gateway's OUTBOUND M2M client, because
  # the gateway calls the runtime presenting the M2M client's token. Without this the runtime's
  # CUSTOM_JWT authorizer rejects the gateway and the gateway target fails to fetch tools.
  runtime_allowed_clients = distinct(concat(
    local.jwt_allowed_clients,
    (local.gateway_enabled && local.auth_is_cognito) ? aws_cognito_user_pool_client.m2m[*].id : [],
    (local.auth_is_cognito && var.enable_oauth_frontdoor_client) ? aws_cognito_user_pool_client.oauth_frontdoor[*].id : [],
  ))

  # ---- Runtime environment variables (the container contract) ---------------------------
  #
  # IMPORTANT: this env is scoped to what the KIT'S example engine actually reads (verified in
  # agents/kb_overlay.py and the runtime entrypoints). A fork that adds a session/memory seam
  # or an in-code JWT seam should extend this map with the matching GAC_* vars (see the
  # commented block below and README) — do NOT add env the engine never consumes.
  #
  # KB env: the engine reads the curated Tier-B overlay from S3. GAC_KB_OVERLAY_KEY is the FULL
  # object key (not a prefix). GAC_ENGINE_ROOT lets the entrypoint locate the engine on sys.path.
  base_env = {
    GAC_KB_OVERLAY     = "s3"
    GAC_KB_BUCKET      = local.kb_bucket_name
    GAC_KB_OVERLAY_KEY = var.kb_overlay_key
    GAC_AWS_REGION     = local.region
    AWS_REGION         = local.region
    AWS_DEFAULT_REGION = local.region
  }

  # Effective guardrail: a Terraform-created starter guardrail (guardrail.tf) takes precedence;
  # otherwise a bring-your-own id. Version follows the same source. Empty when no guardrail.
  effective_guardrail_id = var.create_bedrock_guardrail ? (
    one(aws_bedrock_guardrail.this[*].guardrail_id)
  ) : var.guardrail_id
  effective_guardrail_version = var.create_bedrock_guardrail ? (
    one(aws_bedrock_guardrail_version.this[*].version)
  ) : var.guardrail_version

  # Data-protection env (Tenet 6). The mode + optional managed backend are always set so the
  # unified guard behaves identically in the cloud and locally. GAC_PROPRIETARY_TERMS is only
  # added when non-empty. The guardrail id/version are added only for the guardrails backend.
  data_protection_env = merge(
    {
      GAC_DATA_PROTECTION_MODE = var.data_protection_mode
      GAC_PII_BACKEND          = var.pii_backend
    },
    var.proprietary_terms != "" ? { GAC_PROPRIETARY_TERMS = var.proprietary_terms } : {},
    var.pii_backend == "guardrails" ? {
      GAC_GUARDRAIL_ID      = local.effective_guardrail_id
      GAC_GUARDRAIL_VERSION = local.effective_guardrail_version
      GAC_PII_REGION        = local.region
    } : {},
    var.pii_backend == "comprehend" ? { GAC_PII_REGION = local.region } : {},
  )

  # OPT-IN memory env (READY infrastructure). Only set when a fork enables memory AND wires its
  # engine to consume it. The kit's example engine ignores these, so provisioning memory
  # without a consuming engine leaves the resource idle (see variables.tf enable_memory).
  memory_env = local.memory_enabled ? {
    GAC_SESSION_STORE  = "agentcore-events"
    GAC_MEMORY_ID      = aws_bedrockagentcore_memory.this[0].id
    GAC_MEMORY_REGION  = local.region
    GAC_MEMORY_SHARING = var.memory_sharing_mode
  } : {}

  runtime_env = merge(local.base_env, local.data_protection_env, local.memory_env)

  # ECR image URIs (repos created in ecr.tf).
  orchestrator_image_uri = "${aws_ecr_repository.orchestrator.repository_url}:${var.image_tag}"
  mcp_runtime_image_uri  = "${aws_ecr_repository.mcp_runtime.repository_url}:${var.image_tag}"

  # Shared ECR lifecycle policy: keep the last 5 images, expire the rest.
  ecr_lifecycle_policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}
