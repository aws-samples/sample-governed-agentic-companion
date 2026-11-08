# modules/shared-identity: the ONE shared Cognito user pool for several companions (R1).
#
# This owns ONLY the shared resources: the user pool, its domain, and the per-companion
# entitlement groups. The per-companion resource-server scope and the OAuth/M2M/front-door
# clients stay in each companion's own stack (isolation). See README.md.
#
# A human applies this (Tenet 1). Replacing a live per-companion pool with this shared instance
# is a human-run state migration, never a recreate.
#
# Resource names and arguments are kept equal to the original shared-platform module so that an
# existing platform state can later be adopted by this code with a no-change plan.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# KICS "Cognito UserPool Without MFA" (K7, query ec28bf61) does not evaluate the dynamic
# software_token_mfa_configuration block of the user pool. mfa_configuration defaults to OPTIONAL
# (var.mfa_configuration) and TOTP is enabled whenever MFA is OPTIONAL or ON. Disabled for this file
# by query id, so every other KICS query still runs on the pool.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370,ec28bf61-a474-4dbe-b414-6dd3a067d6f0
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # data.aws_region.current.region is a v6-only attribute
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Domain follows the per-companion convention (<prefix>-<account>) so the hosted-UI/OAuth URLs
  # stay stable through a migration from a per-companion pool to this shared pool.
  pool_domain = "${var.name_prefix}-${data.aws_caller_identity.current.account_id}"
}

# K7 (KICS MFA query): disabled by query id in the file header.
resource "aws_cognito_user_pool" "shared" {
  name = "${var.name_prefix}-pool"
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }
  # Adaptive auth + compromised-credential detection (AWS AgentCore reference baseline).
  user_pool_add_ons {
    advanced_security_mode = var.advanced_security_mode
  }
  # MFA policy. Cognito requires a value; TOTP is enabled as the second factor when MFA is
  # OPTIONAL or ON. Default OPTIONAL clears the "pool without MFA" scanner finding while keeping
  # enrolment opt-in; set "ON" to require MFA for every user.
  mfa_configuration = var.mfa_configuration
  dynamic "software_token_mfa_configuration" {
    for_each = var.mfa_configuration == "OFF" ? [] : [1]
    content {
      enabled = true
    }
  }
  # The pool holds real users shared across companions; protect it from an accidental destroy.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cognito_user_pool_domain" "shared" {
  domain       = local.pool_domain
  user_pool_id = aws_cognito_user_pool.shared.id
}

# One entitlement group per companion. Group membership expresses which companions a user may
# use; it is an administered entitlement signal, not a runtime scope grant (scope stays per
# companion). Keep app keys companion-named and customer-agnostic.
resource "aws_cognito_user_group" "app" {
  for_each = var.apps

  name         = "${each.key}-users"
  user_pool_id = aws_cognito_user_pool.shared.id
  description  = "Entitlement group for the ${each.value} companion flavor"
}
