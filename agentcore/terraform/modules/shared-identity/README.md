# `modules/shared-identity`: one shared Cognito user pool for several companions

Creates the shared identity layer: a single Cognito user pool, its hosted-UI domain
(`<name_prefix>-<account>`), and one entitlement group per companion (`<key>-users`). One user
has one identity across every companion they are entitled to.

> Governance (Tenet 1). A human applies this module (through `../../platform/`). The pool holds
> real users and carries `prevent_destroy`. Moving a live per-companion pool under this module is
> a human-run state migration, never a recreate.

## What is shared, and what is not

Share the pool, not the per-companion clients.

| In this module (one instance) | Stays in each companion stack |
|---|---|
| `aws_cognito_user_pool` (the OIDC issuer) | `aws_cognito_resource_server` and its scope (`<engine_resource_server_identifier>/invoke`) |
| `aws_cognito_user_pool_domain` | the front-door user client, the PKCE OAuth client, the M2M client |
| one `aws_cognito_user_group` per companion | the gateway, runtimes, KB bucket and roles |

Keeping the resource server and clients per companion keeps the companions isolated: companion A's
gateway accepts only its own clients, even though every token comes from the one pool. A second
companion of the same kit in the same pool sets its own `engine_resource_server_identifier`,
because resource-server identifiers are unique within a pool.

## How a companion consumes it

```hcl
# in the companion's gitignored terraform.tfvars
manage_identity_pool  = false
existing_user_pool_id = "<platform output existing_user_pool_id>"
existing_oidc_issuer  = "<platform output existing_oidc_issuer>"
```

The companion still creates its own resource server and clients in the shared pool.

## Inputs

| Variable | Purpose |
|---|---|
| `name_prefix` | Pool name `<prefix>-pool` and domain `<prefix>-<account>`. Required. |
| `apps` | Map of companion key to display label. Required, no default. Keys are companion names, never customer identifiers. |
| `advanced_security_mode` | `ENFORCED` (default), `AUDIT`, or `OFF`. |
| `mfa_configuration` | `OPTIONAL` (default), `ON`, or `OFF`. |

## Outputs

`user_pool_id`, `oidc_issuer`, `oidc_discovery_url`, `user_pool_domain`, `app_groups`.
