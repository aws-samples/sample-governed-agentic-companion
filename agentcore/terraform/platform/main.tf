# Platform stack: the SHARED layer that several companions consume.
#
# It owns:
#   - module.backend  : the remote Terraform state backend (S3 bucket, access-log bucket,
#                       optional DynamoDB lock table)
#   - module.identity : the ONE shared Cognito user pool, its domain and one entitlement group
#                       per companion
#   - module.network  : optional, ONE shared private network (network.tf), off by default
#
# A human applies this FIRST, before any companion that consumes it. It is a separate stack with
# its own state so the shared layer has an independent lifecycle.
#
# The module names "backend" and "identity" are kept so that an existing platform state with the
# same addresses can be adopted by this code (docs/DEPLOY.md "Adopting an existing platform
# state"). Do not rename them.
#
# Governance (Tenet 1): a human applies this. No agent applies it or migrates live state.

module "backend" {
  source            = "../modules/backend-bootstrap"
  state_bucket_name = var.state_bucket_name
  lock_table_name   = var.lock_table_name
}

# Neutral prefix so the domain (<prefix>-<account>) does not collide with a single companion's own
# pool domain.
module "identity" {
  source      = "../modules/shared-identity"
  name_prefix = var.identity_name_prefix
  apps        = var.apps
}
