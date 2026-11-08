# `platform/`: the shared layer several companions consume

A separate Terraform root with its own state. It owns the components that one account shares
across companions:

| Module call | Module | What it creates |
|---|---|---|
| `module.backend` | `../modules/backend-bootstrap` | Remote state bucket (+ access-log bucket, optional lock table) |
| `module.identity` | `../modules/shared-identity` | The one shared Cognito pool, its domain, one entitlement group per companion |
| `module.network` *(optional, `enable_private_network = true`)* | `../modules/private-network` | ONE shared private network: VPC, two private subnets, interface endpoints, S3 gateway endpoint, endpoint SGs, optional Route 53 Resolver inbound endpoint |

Exactly one stack owns the shared pool. Companions consume it with `manage_identity_pool = false`.

> Governance (Tenet 1). A human runs every `init`, `plan`, `apply` and state migration here. No
> agent applies this stack or moves its state.

## Apply order (human-run)

1. `cp terraform.tfvars.example terraform.tfvars` and fill in the placeholders (gitignored).
2. `terraform init && terraform plan -out platform.tfplan`, review, `terraform apply platform.tfplan`.
   The first apply uses local state because the bucket does not exist yet.
3. `cp backend.tf.example backend.tf`, set the bucket from `terraform output state_bucket`, and run
   `terraform init -migrate-state`.
4. For each companion: copy `../backend.tf.example` to `../backend.tf` with its own key, and copy
   the values from `terraform output` (`existing_user_pool_id`, `existing_oidc_issuer`) into the
   companion's gitignored `terraform.tfvars`.

## Optional shared private network

Off by default (`enable_private_network = false`). When on, it needs `private_client_cidrs` (a
guard refuses it otherwise). Endpoint policies are account-scoped, because one shared endpoint
cannot name a single companion's resources: the gateway endpoint admits this account's gateways,
the data-plane endpoint the explicit companion action list on this account's AgentCore resources,
and the S3 gateway endpoint the ECR layer bucket plus `kb_bucket_arn_patterns` (default: the
companion auto-name pattern), with `aws:ResourceAccount` set to this account. List exact bucket
ARNs in `kb_bucket_arn_patterns` to narrow it, and add any companion with a custom
`kb_bucket_name`.

A shared network is one network boundary for several companions (`docs/SECURITY.md` SD-27).
Settings that belong to the shared endpoints are set here, not on a consumer:
`dataplane_endpoint_extra_actions`, `allow_memory_dataplane_actions`,
`extra_interface_endpoint_services`.

A consumer copies `terraform output -json companion_consumer_inputs` into its gitignored tfvars.
Pass the whole `existing_interface_endpoint_ids` map: a missing key makes the companion create a
second private-DNS endpoint for that service and the apply fails.

## A companion's entitlement group (3.4.0)

From 3.4.0 a companion requires its users to be in its entitlement group, `<name_prefix>-users`
by default (the token's `cognito:groups`). On the shared pool the companion never creates that
group: it only names it (`entitlement_group_name`) and, with `check_entitlement_group_exists =
true` (the default), its plan reads the pool's groups (read-only `ListGroups`) and fails clearly
when the group is missing. A planning identity without `cognito-idp:ListGroups` sets
`check_entitlement_group_exists = false`, and the group must then exist before the first user
test. The pool's owner creates the group in one of three ways (all human-run):

- **Preferred: the `apps` map.** Add the companion's key to `apps` in this stack's tfvars (for
  example `gac = "Governed Agentic Companion"`); `modules/shared-identity` creates `<key>-users`
  (`aws_cognito_user_group.app`, one per `apps` key). Apply this stack first, then the companion.
- **Manual.** `aws cognito-idp create-group --user-pool-id <pool> --group-name <prefix>-users`.
- **Adopting a hand-made group into this state later.** Adding the key to `apps` while the group
  already exists fails the apply with `GroupExistsException`. First add an `import` block
  (`to = module.identity.aws_cognito_user_group.app["<key>"]`, `id = "<pool id>/<prefix>-users"`),
  review the plan (it must show one import and no create), then apply.

Users are added by the pool's owner with `aws cognito-idp admin-add-user-to-group`. The
deployed-stack E2E (`scripts/e2e_deployed.py`) also needs two test users, U0 in no group and
optionally U1 in a test-only group no companion names; never put a test user in another
companion's group. See `docs/DEPLOY.md`, "Entitlement group" and "Deny-first E2E".

## Wiring choice

Values are copied into each companion's tfvars. The companion root stays standalone and can run
`terraform init -backend=false` without a platform state. A fork that prefers
`terraform_remote_state` can add one to the companion root:

```hcl
data "terraform_remote_state" "platform" {
  backend = "s3"
  config = {
    bucket = "companion-tfstate-<ACCOUNT_ID>-<REGION>"
    key    = "platform/terraform.tfstate"
    region = "<REGION>"
  }
}
# then, for example: existing_user_pool_id = data.terraform_remote_state.platform.outputs.existing_user_pool_id
```

## Adopting an existing platform state

See `docs/DEPLOY.md` "Adopting an existing platform state (human-run)". The module names
`backend` and `identity`, and every resource name, are kept so that a platform state with the
same addresses plans no replacement. Since 3.2.2 the plan adds three hardening resources in
`module.backend` (an access-log lifecycle and two EventBridge notifications) and updates two in
place (the access-log bucket's encryption, SSE-KMS to SSE-S3, and its bucket policy, which gains
the log-delivery grant). The new lifecycle expires existing access logs older than 365 days, and
noncurrent log versions after 30 days, on the adopted bucket.

## Tests

`terraform test` (Terraform 1.7 or later) runs plan-only cases with mocked providers in
`tests/platform.tftest.hcl`; no AWS credentials are needed.
