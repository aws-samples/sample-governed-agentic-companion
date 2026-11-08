# Amazon Cognito IdP — created ONLY when auth_mode = "cognito".
#
# Provides:
#   - a user pool (the OIDC issuer the inbound JWT authorizer trusts)
#   - a front-door USER client (the 'aud' the builder's front door presents; ADMIN_USER_
#     PASSWORD_AUTH is enabled for the local stdio bridge flow)
#   - an optional PKCE PUBLIC client for the IDE OAuth path
#   - a resource server + custom scope + an M2M (client_credentials) app client for the
#     gateway->runtime OUTBOUND hop
#
# Hardening follows the AWS AgentCore reference samples: AdvancedSecurityMode ENFORCED
# (adaptive auth + compromised-credential detection), a strong password policy, admin-only
# user creation, and short-lived access/id tokens. MFA is configurable (default OPTIONAL: TOTP
# is available but not forced; set cognito_mfa_mode="ON" for production).
#
# For auth_mode = "entraid" or "none", none of these are created (count = 0).
#
# KICS "Cognito UserPool Without MFA" (K7, query ec28bf61) does not evaluate the dynamic
# software_token_mfa_configuration block of the user pool. mfa_configuration defaults to OPTIONAL
# (var.cognito_mfa_mode) and TOTP is enabled whenever MFA is OPTIONAL or ON. Disabled for this file
# by query id, so every other KICS query still runs on the pool.
# kics-scan disable=ec28bf61-a474-4dbe-b414-6dd3a067d6f0

locals {
  # Two DISTINCT gates (a shared pool consumed by several companions):
  #   pool_count   — create the POOL + DOMAIN only when this deployment OWNS identity
  #                  (manage_identity_pool=true). When consuming a shared pool, not created.
  #   client_count — create this flavor's OWN resource-server scope + clients WHENEVER cognito
  #                  is in use, OWNED OR CONSUMED (per-flavor isolation: this companion's <engine_resource_server_identifier>/invoke
  #                  scope + its own clients live IN the shared pool).
  # Default manage_identity_pool=true => both ==1 => today's behavior exactly.
  pool_count   = (local.auth_is_cognito && var.manage_identity_pool) ? 1 : 0
  client_count = local.auth_is_cognito ? 1 : 0

  # The pool clients/resource-server attach to: created pool when owning, shared pool when consuming.
  effective_pool_id = var.manage_identity_pool ? one(aws_cognito_user_pool.this[*].id) : var.existing_user_pool_id

  cognito_domain = "${var.name_prefix}-${local.account_id}"
  # Resource-server identifier (scope <identifier>/invoke). Unique within a user pool: a second
  # companion of this kit consuming the same shared pool sets its own value.
  m2m_resource_server = var.engine_resource_server_identifier
  m2m_scope_name      = "invoke"

  # ---- Entitlement group (D26.2) ------------------------------------------------------------
  # The authorizers require the token's group claim to CONTAIN this group (gateway.tf,
  # runtimes.tf); the interceptor re-checks it. The own path creates the group in the owned pool;
  # the consume path never creates it (the shared pool's owner does) and only checks at plan time
  # that it exists. An own-path deployer with manage_entitlement_group = false creates the group
  # elsewhere before the first user test (no check: the owned pool does not exist at first plan).
  entitlement_group        = var.entitlement_group_name != "" ? var.entitlement_group_name : "${var.name_prefix}-users"
  entitlement_enforced     = local.inbound_jwt_enabled && var.require_entitlement_group
  manage_entitlement_group = var.manage_entitlement_group == null ? var.manage_identity_pool : var.manage_entitlement_group
  entitlement_group_count  = (local.entitlement_enforced && local.auth_is_cognito && local.manage_entitlement_group) ? 1 : 0
  entitlement_unmanaged    = local.entitlement_enforced && local.auth_is_cognito && !local.manage_entitlement_group
  entitlement_check_count  = (local.entitlement_unmanaged && !var.manage_identity_pool && var.check_entitlement_group_exists) ? 1 : 0
  # The claim the authorizers' custom_claim blocks read: the group list (STRING_ARRAY). Kept in a
  # map so those blocks hold no quoted literal on a claim-name line (detect-secrets keyword rule).
  entitlement_claim = {
    name       = var.entitlement_claim_name
    value_type = "STRING_ARRAY"
  }
}

# The entitlement group in the OWNED pool (guards.tf forbids creating it in a shared pool).
resource "aws_cognito_user_group" "entitlement" {
  count        = local.entitlement_group_count
  name         = local.entitlement_group
  user_pool_id = local.effective_pool_id
  description  = "Entitlement group for this companion"
}

# Consume path: read the shared pool's groups (ListGroups, read-only; no state ownership of
# shared-pool objects) and fail the plan when the entitlement group is missing.
data "aws_cognito_user_groups" "entitlement" {
  count        = local.entitlement_check_count
  user_pool_id = local.effective_pool_id

  lifecycle {
    postcondition {
      condition     = contains([for g in self.groups : g.group_name], local.entitlement_group)
      error_message = "entitlement group ${local.entitlement_group} not found in the shared user pool; the pool's owner creates it (platform/ apps map or a manual step), see docs/DEPLOY.md"
    }
  }
}

# K7 (KICS MFA query): disabled by query id in the file header.
resource "aws_cognito_user_pool" "this" {
  count = local.pool_count
  name  = "${var.name_prefix}-pool"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # Adaptive auth + compromised-credential detection (AWS reference default). Configurable via
  # var.cognito_advanced_security_mode; ENFORCED is the recommended baseline.
  user_pool_add_ons {
    advanced_security_mode = var.cognito_advanced_security_mode
  }

  # MFA policy. Cognito requires an mfa_configuration value; TOTP is enabled as the second
  # factor when MFA is OPTIONAL or ON. The default OPTIONAL makes TOTP available without forcing
  # it on a dev front door; set cognito_mfa_mode="ON" for production (the compensating controls
  # are the strong password policy + advanced security mode above).
  mfa_configuration = var.cognito_mfa_mode

  dynamic "software_token_mfa_configuration" {
    for_each = var.cognito_mfa_mode == "OFF" ? [] : [1]
    content {
      enabled = true
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = var.tags
}

resource "aws_cognito_user_pool_domain" "this" {
  count        = local.pool_count
  domain       = local.cognito_domain
  user_pool_id = aws_cognito_user_pool.this[0].id
}

# Front-door USER client (public front door presents tokens from this client). Created in the
# OWNED or the SHARED pool (effective_pool_id) — a per-flavor client either way.
resource "aws_cognito_user_pool_client" "frontdoor" {
  count        = local.client_count
  name         = "${var.name_prefix}-frontdoor"
  user_pool_id = local.effective_pool_id

  generate_secret = true # pragma: allowlist secret

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH", # the local stdio bridge (cognito_token.py) uses this
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Defense-in-depth token hygiene (matches the AWS reference samples).
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true # pragma: allowlist secret

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  # K1: KICS "Generic Token" reports each line below for its attribute name (access_token,
  # id_token, refresh_token) and unit value ("minutes", "days"); no token value is in this file.
  # Each marker covers the one attribute line after it.
  token_validity_units {
    # kics-scan ignore-line
    access_token = "minutes" # pragma: allowlist secret
    # kics-scan ignore-line
    id_token = "minutes"
    # kics-scan ignore-line
    refresh_token = "days" # pragma: allowlist secret
  }

  callback_urls                = length(var.cognito_frontdoor_callback_urls) > 0 ? var.cognito_frontdoor_callback_urls : null
  supported_identity_providers = ["COGNITO"]
}

# PKCE PUBLIC client for the Kiro/Claude IDE OAuth path (no secret — the IDE runs the
# authorization-code + PKCE flow itself and AUTO-REFRESHES the token, removing the hourly
# manual re-mint of the static-header path). Created only when auth_mode=cognito AND
# enable_oauth_frontdoor_client=true. The token carries the engine resource-server scope so
# the gateway/runtime CUSTOM_JWT authorizer accepts it.
resource "aws_cognito_user_pool_client" "oauth_frontdoor" {
  count        = local.client_count > 0 && var.enable_oauth_frontdoor_client ? 1 : 0
  name         = "${var.name_prefix}-oauth-frontdoor"
  user_pool_id = local.effective_pool_id

  generate_secret = false # public client — PKCE, no secret (Kiro IDE supports public/PKCE only)  # pragma: allowlist secret

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  # openid for OIDC discovery + the engine scope so the access token's audience is accepted
  # by the gateway/runtime authorizer (same scope the M2M client uses).
  allowed_oauth_scopes         = ["openid", "${local.m2m_resource_server}/${local.m2m_scope_name}"]
  supported_identity_providers = ["COGNITO"]

  callback_urls = var.oauth_frontdoor_callback_urls
  # Refresh so the IDE can silently refresh (no re-login each hour).
  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true # pragma: allowlist secret

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  # K1: KICS "Generic Token" reports each line below for its attribute name (access_token,
  # id_token, refresh_token) and unit value ("minutes", "days"); no token value is in this file.
  # Each marker covers the one attribute line after it.
  token_validity_units {
    # kics-scan ignore-line
    access_token = "minutes" # pragma: allowlist secret
    # kics-scan ignore-line
    id_token = "minutes"
    # kics-scan ignore-line
    refresh_token = "days" # pragma: allowlist secret
  }

  depends_on = [aws_cognito_resource_server.engine]
}

# Resource server + scope for the M2M (gateway->runtime) client_credentials grant.
resource "aws_cognito_resource_server" "engine" {
  count        = local.client_count
  identifier   = local.m2m_resource_server
  name         = "Governed Companion Engine"
  user_pool_id = local.effective_pool_id

  scope {
    scope_name        = local.m2m_scope_name
    scope_description = "Invoke the governed companion engine runtime"
  }
}

# M2M client_credentials app client (the gateway's OUTBOUND credential). Public mode keeps
# today's count. Private mode creates none: the gateway uses SigV4, so no client secret lands in
# state and nothing can mint client_credentials tokens on the public token endpoint.
resource "aws_cognito_user_pool_client" "m2m" {
  count        = local.client_count > 0 && (!local.network_private || local.gateway_outbound_oauth) ? 1 : 0
  name         = "${var.name_prefix}-m2m"
  user_pool_id = local.effective_pool_id

  generate_secret                      = true # pragma: allowlist secret
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${local.m2m_resource_server}/${local.m2m_scope_name}"]
  supported_identity_providers         = ["COGNITO"]

  depends_on = [aws_cognito_resource_server.engine]
}
