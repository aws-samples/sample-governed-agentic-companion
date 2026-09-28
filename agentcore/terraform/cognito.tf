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
# user creation, and short-lived access/id tokens. MFA is configurable (default OFF for a
# low-friction dev front door; set cognito_mfa_mode="ON" for production).
#
# For auth_mode = "entraid" or "none", none of these are created (count = 0).

locals {
  cognito_count       = local.auth_is_cognito ? 1 : 0
  cognito_domain      = "${var.name_prefix}-${local.account_id}"
  m2m_resource_server = "gac-engine"
  m2m_scope_name      = "invoke"
}

resource "aws_cognito_user_pool" "this" {
  count = local.cognito_count
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
  # factor when MFA is OPTIONAL or ON. Default OFF keeps the dev front door low-friction —
  # set cognito_mfa_mode="ON" for production (the compensating controls are the strong
  # password policy + advanced security mode above).
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
  count        = local.cognito_count
  domain       = local.cognito_domain
  user_pool_id = aws_cognito_user_pool.this[0].id
}

# Front-door USER client (public front door presents tokens from this client).
resource "aws_cognito_user_pool_client" "frontdoor" {
  count        = local.cognito_count
  name         = "${var.name_prefix}-frontdoor"
  user_pool_id = aws_cognito_user_pool.this[0].id

  generate_secret = true

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH", # the local stdio bridge (cognito_token.py) uses this
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Defense-in-depth token hygiene (matches the AWS reference samples).
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
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
  count        = local.cognito_count > 0 && var.enable_oauth_frontdoor_client ? 1 : 0
  name         = "${var.name_prefix}-oauth-frontdoor"
  user_pool_id = aws_cognito_user_pool.this[0].id

  generate_secret = false # public client — PKCE, no secret (Kiro IDE supports public/PKCE only)

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
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  depends_on = [aws_cognito_resource_server.engine]
}

# Resource server + scope for the M2M (gateway->runtime) client_credentials grant.
resource "aws_cognito_resource_server" "engine" {
  count        = local.cognito_count
  identifier   = local.m2m_resource_server
  name         = "Governed Companion Engine"
  user_pool_id = aws_cognito_user_pool.this[0].id

  scope {
    scope_name        = local.m2m_scope_name
    scope_description = "Invoke the governed companion engine runtime"
  }
}

# M2M client_credentials app client (the gateway's OUTBOUND credential).
resource "aws_cognito_user_pool_client" "m2m" {
  count        = local.cognito_count
  name         = "${var.name_prefix}-m2m"
  user_pool_id = aws_cognito_user_pool.this[0].id

  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${local.m2m_resource_server}/${local.m2m_scope_name}"]
  supported_identity_providers         = ["COGNITO"]

  depends_on = [aws_cognito_resource_server.engine]
}
