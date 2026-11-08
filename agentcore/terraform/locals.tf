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
  # cognito: owned => derived from the created pool; consume => the supplied shared-pool issuer.
  # entraid: derived from the supplied discovery URL.
  jwt_issuer = local.auth_is_cognito ? (
    var.manage_identity_pool ?
    "https://cognito-idp.${local.region}.amazonaws.com/${one(aws_cognito_user_pool.this[*].id)}" :
    var.existing_oidc_issuer
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

  # The ORCHESTRATOR's inbound allow-list: the front-door clients (static-header and PKCE OAuth).
  # The gateway's M2M client is not listed: it never calls the orchestrator.
  orchestrator_allowed_clients = distinct(concat(
    local.jwt_allowed_clients,
    (local.auth_is_cognito && var.enable_oauth_frontdoor_client) ? aws_cognito_user_pool_client.oauth_frontdoor[*].id : [],
  ))

  # The MCP runtime's inbound allow-list. When the gateway calls the runtime with the OUTBOUND M2M
  # client's token (public mode), ONLY that client is allowed: client-credentials tokens carry no
  # groups, so the gateway is the entitlement point and a user token sent directly is refused.
  # Without that hop (no gateway, or entraid) the runtime takes the orchestrator's list and claim
  # rule. In private mode with a gateway the runtime has no JWT authorizer (SigV4, gateway role).
  mcp_runtime_allowed_clients = local.gateway_outbound_oauth ? aws_cognito_user_pool_client.m2m[*].id : local.orchestrator_allowed_clients

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

  # Trace content (private mode). The ADOT distro pinned in both images auto-loads its MCP
  # instrumentation (aws_mcp) when AGENT_OBSERVABILITY_ENABLED=true; it writes tool-call
  # arguments (and has a tool-result code path) into spans, which are readable through IAM from
  # any network. Private mode disables it unless trace_tool_content=true. Public mode is
  # unchanged (empty map).
  observability_env = local.network_private && !var.trace_tool_content ? { OTEL_PYTHON_DISABLED_INSTRUMENTATIONS = "aws_mcp" } : {}

  # Opt-in Bedrock diagram planner (D11.3). Set only when the planner is "bedrock"; the guardrail
  # values equal the data-protection ones, so the merge cannot conflict.
  diagram_env = var.diagram_planner == "bedrock" ? {
    GAC_DIAGRAM_PLANNER          = "bedrock"
    GAC_DIAGRAM_PLANNER_MODEL_ID = var.diagram_planner_model_id
    GAC_GUARDRAIL_ID             = local.effective_guardrail_id
    GAC_GUARDRAIL_VERSION        = local.effective_guardrail_version
  } : {}

  runtime_env = merge(local.base_env, local.data_protection_env, local.memory_env, local.observability_env,
  local.diagram_env, var.diagram_enabled ? {} : { GAC_DIAGRAM_ENABLED = "false" })

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

# ---- Network access (private by default) ------------------------------------------------
# Every private-mode resource in network.tf (and module.private_network) / resource_policies.tf
# and every private-only
# policy statement is gated on these locals, so network_access="public" creates none of them.
locals {
  network_private      = var.network_access == "private"
  create_vpc           = local.network_private && var.create_vpc
  runtime_network_mode = local.network_private ? "VPC" : "PUBLIC"

  # Private mode with a gateway: the gateway reaches the MCP runtime with SigV4 (gateway role)
  # and the MCP runtime has no JWT authorizer. Public mode keeps the OAuth M2M hop.
  mcp_runtime_iam_inbound = local.network_private && local.gateway_enabled
  gateway_outbound_oauth  = local.gateway_enabled && local.auth_is_cognito && !local.network_private

  # Effective VPC inputs: kit-created sandbox VPC, or the operator's existing VPC.
  # The created VPC comes from module.private_network (modules/private-network).
  vpc_id_effective                  = local.create_vpc ? module.private_network[0].vpc_id : var.vpc_id
  private_subnet_ids_effective      = local.create_vpc ? module.private_network[0].private_subnet_ids : var.private_subnet_ids
  private_route_table_ids_effective = local.create_vpc ? module.private_network[0].private_route_table_ids : var.private_route_table_ids

  # Developer/IDE networks allowed to reach the client endpoints. A sandbox VPC defaults to
  # its own CIDR; an existing VPC needs explicit CIDRs (guards.tf).
  client_cidrs_effective = length(var.private_client_cidrs) > 0 ? var.private_client_cidrs : (local.create_vpc ? [var.vpc_cidr] : [])

  # Interface endpoints the kit creates (minus the ones the operator reuses).
  managed_endpoint_keys = local.network_private ? setsubtract(toset(concat(
    ["ecr.api", "ecr.dkr", "logs", "bedrock-agentcore"],
    local.gateway_enabled ? ["bedrock-agentcore.gateway"] : [],
    var.enable_bedrock_runtime_endpoint ? ["bedrock-runtime"] : [],
    var.enable_xray_endpoint ? ["xray"] : [],
    var.pii_backend == "comprehend" ? ["comprehend"] : [],
    var.extra_interface_endpoint_services,
  )), keys(var.existing_interface_endpoint_ids)) : toset([])

  # Endpoints that sit in the client SG (runtime SG + approved client CIDRs). Every other
  # managed endpoint sits in the runtime-only SG.
  client_endpoint_keys = concat(
    ["bedrock-agentcore"],
    local.gateway_enabled ? ["bedrock-agentcore.gateway"] : [],
    var.bedrock_runtime_client_access && var.enable_bedrock_runtime_endpoint ? ["bedrock-runtime"] : [],
  )

  # Endpoint ids by key (created plus reused).
  vpce_id            = merge(var.existing_interface_endpoint_ids, local.module_endpoint_ids)
  dataplane_vpce_ids = compact(concat([try(local.vpce_id["bedrock-agentcore"], "")], var.additional_dataplane_vpce_ids))
  gateway_vpce_ids   = compact(concat([try(local.vpce_id["bedrock-agentcore.gateway"], "")], var.additional_gateway_vpce_ids))
  # A reused S3 gateway endpoint applies to an existing VPC only (the sandbox VPC gets its own).
  reuse_s3_endpoint  = local.network_private && !local.create_vpc && var.existing_s3_gateway_endpoint_id != ""
  s3_gateway_vpce_id = local.reuse_s3_endpoint ? var.existing_s3_gateway_endpoint_id : (local.network_private ? module.private_network[0].s3_gateway_endpoint_id : null)
  ecr_vpce_ids       = compact([try(local.vpce_id["ecr.dkr"], ""), try(local.vpce_id["ecr.api"], "")])

  # Shared data-plane endpoint detection. A reused bedrock-agentcore endpoint (for example the
  # platform's shared network) or any additional_dataplane_vpce_ids (a hub VPC) can carry other
  # companions' traffic, so aws:SourceVpce no longer separates companions on those endpoints.
  # Drives the Memory DenyOtherPrincipalsOnSharedEndpoint statement and guard G-none-shared.
  dataplane_endpoint_reused = local.network_private && contains(keys(var.existing_interface_endpoint_ids), "bedrock-agentcore")
  dataplane_endpoint_shared = local.dataplane_endpoint_reused || (local.network_private && length(var.additional_dataplane_vpce_ids) > 0)
  # Any client endpoint (gateway or data plane) reused: the probe then needs the reused client SGs.
  client_endpoints_reused = length(setintersection(toset(["bedrock-agentcore", "bedrock-agentcore.gateway"]), toset(keys(var.existing_interface_endpoint_ids)))) > 0

  # Data-plane actions allowed through the kit's bedrock-agentcore endpoint (explicit list, not
  # bedrock-agentcore:*). InvokeAgentRuntime is what the bridge calls; the token actions are the
  # AgentCore Identity calls the SDK can make from inside a runtime.
  dataplane_base_actions = [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:GetWorkloadAccessToken",
    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    "bedrock-agentcore:GetResourceOauth2Token",
    "bedrock-agentcore:GetResourceApiKey",
  ]
  # The Memory data-plane actions (resource-based-policies.html). Shared by the data-plane
  # endpoint policy and the Memory resource policy so the two lists cannot drift.
  memory_dataplane_actions = [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:GetEvent",
    "bedrock-agentcore:DeleteEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:ListActors",
    "bedrock-agentcore:ListSessions",
    "bedrock-agentcore:GetMemoryRecord",
    "bedrock-agentcore:ListMemoryRecords",
    "bedrock-agentcore:RetrieveMemoryRecords",
    "bedrock-agentcore:DeleteMemoryRecord",
    "bedrock-agentcore:BatchCreateMemoryRecords",
    "bedrock-agentcore:BatchUpdateMemoryRecords",
    "bedrock-agentcore:BatchDeleteMemoryRecords",
    "bedrock-agentcore:StartMemoryExtractionJob",
    "bedrock-agentcore:ListMemoryExtractionJobs",
  ]
  # The full Agent Runtime action list (resource-based-policies.html). Every runtime Deny uses
  # it, so no runtime action is reachable outside the approved path.
  runtime_policy_actions = [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:InvokeAgentRuntimeForUser",
    "bedrock-agentcore:InvokeAgentRuntimeCommand",
    "bedrock-agentcore:InvokeAgentRuntimeCommandShell",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream",
    "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser",
    "bedrock-agentcore:StopRuntimeSession",
    "bedrock-agentcore:GetAgentCard",
  ]
}

# ---- Knowledge admins (exempt from the KB bucket and ECR VPC-endpoint-only denies) --------
# The current terraform runner is auto-added only when no listed ARN (glob-aware) matches it,
# so a listed runner causes no policy churn; an unlisted runner gets a plan warning.
data "aws_iam_session_context" "current" {
  count = local.network_private ? 1 : 0 # resolves an assumed-role ARN with iam:GetRole
  arn   = data.aws_caller_identity.current.arn
}

locals {
  runner_arn = try(data.aws_iam_session_context.current[0].issuer_arn, "")
  # Glob -> anchored regex: escape the regex metacharacters an IAM ARN can contain ('.', '+'),
  # then map IAM globs ('*' any run, '?' one char).
  admin_arn_regexes = [for a in var.knowledge_admin_principal_arns :
  "^${replace(replace(replace(replace(a, ".", "\\."), "+", "\\+"), "*", ".*"), "?", ".")}$"]
  runner_listed                  = local.runner_arn == "" ? true : anytrue([for r in local.admin_arn_regexes : can(regex(r, local.runner_arn))])
  knowledge_admin_arns_effective = local.runner_listed ? var.knowledge_admin_principal_arns : concat(var.knowledge_admin_principal_arns, [local.runner_arn])
}
