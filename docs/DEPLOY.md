<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Deploy & implementation — Amazon Bedrock AgentCore (human-run, Tenet 1)

This is the implementation guide for putting the companion on Amazon Bedrock AgentCore with the
Terraform stack under [`../agentcore/terraform/`](../agentcore/terraform/). The companion runs
locally with no AWS; this page is how you provision the cloud footprint so builders reach it
through the AgentCore Gateway.

> **A human runs every AWS mutation (Tenet 1).** No agent in this repo runs Terraform, builds
> images, or invokes against AWS. Terraform *prepares* the plan; a human runs `terraform apply`.

> **⚠️ Sample code — not for production as-is.** The IAM, identity, network, and credential
> settings are illustrative. Review them against your account and threat model, and see the
> accepted-security-debt table in [`SECURITY.md`](SECURITY.md) before production use.

## Prerequisites

- **Terraform ≥ 1.6**, the **AWS CLI**, and **Docker** (with `buildx`) on PATH.
- AWS credentials for a human operator (`AWS_PROFILE` / `AWS_REGION`) able to create ECR, IAM, S3,
  KMS, Cognito, Secrets Manager, Lambda, and Bedrock AgentCore resources.
- A Region where **AgentCore Runtime is GA** and your Bedrock **model / inference profile** is
  available. Confirm the exact inference-profile id for your Region first — a `us.` profile only
  resolves in US Regions; use the `au.`/`apac.` profile in Asia-Pacific, etc.:
  ```bash
  aws bedrock list-inference-profiles --region <REGION> \
    --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].[inferenceProfileId,status]" --output text
  ```
- Amazon Bedrock **model access enabled** for your chosen model in that Region.

### Private mode prerequisites (`network_access = "private"`, the default)

- **VPC inputs.** Either `create_vpc = true` (a one-apply SANDBOX VPC: two private subnets in
  AgentCore-supported AZs, no internet gateway, no NAT), or your own VPC: `vpc_id`, at least two
  `private_subnet_ids` in different
  [supported AZ IDs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html),
  and the `private_route_table_ids` that serve them. The VPC needs DNS hostnames and DNS
  resolution enabled (private DNS for interface endpoints:
  [interface endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/interface-endpoints.html)).
- **Prefer a VPC dedicated to the companion, with dedicated route tables.** Endpoints the kit creates
  with private DNS take over the regional service names for every workload in the VPC, and the S3
  gateway endpoint applies its restrictive policy to every subnet on the route tables you supply.
  In a shared VPC, reuse your central endpoints with `existing_interface_endpoint_ids`,
  `existing_endpoint_security_group_ids` and `existing_s3_gateway_endpoint_id`.
- **A reused S3 gateway endpoint** must allow, for principal `*`: `s3:GetObject` on
  `arn:aws:s3:::prod-<region>-starport-layer-bucket/*` (ECR image layers,
  [agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)),
  `s3:GetObject` and `s3:PutObject` on `<kb_bucket_arn>/<kb_prefix>/*`, and `s3:ListBucket` on
  `<kb_bucket_arn>`.
- **`private_client_cidrs`**: the IPv4 ranges your IDEs connect from (VPN client pool, on-premises
  ranges, in-VPC desktops). `/0` and anything wider than `/8` are rejected. Plan the client network
  path first ([`FRONT-DOORS.md`](FRONT-DOORS.md) "Private mode access (default)").
- **`knowledge_admin_principal_arns`** (required, non-empty): every IAM role or user that runs
  terraform, every CI role that pushes images, and every curator who promotes the Tier-B overlay.
  Use specific roles or narrow patterns (for example an SSO permission-set role
  `arn:aws:iam::<account-id>:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_<set>_*`), never
  `role/*` (rejected by validation). Unlisted principals cannot read the KB bucket or pull/push the
  images from outside the VPC.
- **Curator identity policy** for the KB key: `kms:Decrypt`, `kms:Encrypt`, `kms:GenerateDataKey*`
  and `kms:DescribeKey` on the `kb_kms_key_arn` output, plus `s3:GetObject`/`s3:PutObject` on
  `<kb_bucket_arn>/<kb_prefix>/*` and `s3:ListBucket` on the bucket. The key policy's
  `KnowledgeAdminS3Use` statement only delegates to IAM; the identity policy must also allow it
  ([key-policy-default.html](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html)).
- **Deployer permissions**, in addition to the list above:
  - `iam:CreateServiceLinkedRole` for
    `arn:aws:iam::*:role/aws-service-role/network.bedrock-agentcore.amazonaws.com/AWSServiceRoleForBedrockAgentCoreNetwork`
    (AgentCore creates the runtime ENIs with this role;
    [agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)).
  - `iam:GetRole` on the runner's own role (`data.aws_iam_session_context` resolves the
    assumed-role ARN with it).
  - `ec2:CreateVpcEndpoint`, `ec2:ModifyVpcEndpoint`, `ec2:DeleteVpcEndpoints`,
    `ec2:DescribeVpcEndpoints`, `ec2:DescribeVpcEndpointServices`, `ec2:DescribePrefixLists`,
    `ec2:DescribeRouteTables`, `ec2:DescribeSubnets`, `ec2:DescribeVpcs`,
    `ec2:DescribeVpcAttribute`, the security-group create/authorize/revoke/delete/describe actions,
    and, for `create_vpc`, the VPC, subnet and route-table create/delete actions.
  - `bedrock-agentcore:PutResourcePolicy`, `GetResourcePolicy` and `DeleteResourcePolicy`,
    `ecr:SetRepositoryPolicy`/`GetRepositoryPolicy`, and `s3:PutBucketPolicy`/`GetBucketPolicy`.
  - Membership in `knowledge_admin_principal_arns`.

## What gets deployed

The stack provisions two AgentCore runtimes (HTTP `/invocations` + stateless MCP `/mcp`), two ECR
repos, a shared least-privilege execution role, a private CMK-encrypted S3 knowledge base, and —
as independent **opt-ins** — Cognito identity, an AgentCore Gateway + M2M credential + target, an
identity interceptor Lambda, AgentCore Memory, and a Bedrock Guardrail. See
[`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) for the full resource
inventory and every input, and [`COST.md`](COST.md) for what each opt-in costs.

In private mode (default) it also creates interface endpoints with private DNS (ECR api/dkr,
CloudWatch Logs, AgentCore data plane, AgentCore Gateway when deployed, Bedrock Runtime, X-Ray,
Comprehend when used), an S3 gateway endpoint, three security groups, the AgentCore resource
policies, and, with `create_vpc = true`, a minimal private VPC. Public mode creates none of these.

## Adoption paths

The Terraform has two roots: the companion stack (`agentcore/terraform/`) and an optional shared
platform stack (`agentcore/terraform/platform/`). Both use reusable modules under
`agentcore/terraform/modules/` (`backend-bootstrap`, `shared-identity`, `private-network`). Pick
one path per account.

- **Path A: shared platform first.** (1) A human applies `platform/`: the remote-state bucket, the
  one shared Cognito pool with one entitlement group per companion, and optionally ONE shared
  private network (`enable_private_network = true`). (2) The human moves the platform state into
  the bucket (`platform/backend.tf.example`, then `terraform init -migrate-state`). (3) Each
  companion copies `backend.tf.example` to `backend.tf` with its own key
  (`companions/<name>/terraform.tfstate`), copies the values from
  `terraform output -json companion_consumer_inputs` into its gitignored `terraform.tfvars`
  (`manage_identity_pool = false`, the pool id and issuer, and the network values when the shared
  network is on), sets its own `engine_resource_server_identifier`, and applies. Exactly one stack
  owns the pool.
- **Path B: standalone companion (the default, unchanged).** `manage_identity_pool = true` and
  either `create_vpc = true` or your own VPC. No platform stack.

### Consuming the shared network

Set `network_access = "private"`, `create_vpc = false`, `vpc_id`, `private_subnet_ids`,
`private_route_table_ids`, `existing_s3_gateway_endpoint_id`, the WHOLE
`existing_interface_endpoint_ids` map (a missing key makes the companion create a second
private-DNS endpoint for that service, and the apply fails), `existing_endpoint_security_group_ids`,
`manage_existing_endpoint_ingress = true` (the companion then adds TCP 443 ingress from its runtime
SG on the platform endpoint SGs), `private_client_cidrs`, and `knowledge_admin_principal_arns`.
`existing_client_endpoint_security_group_ids` is needed only with the private access probe.

On a shared network the endpoint policies belong to the platform. Set
`dataplane_endpoint_extra_actions`, `allow_memory_dataplane_actions` and
`extra_interface_endpoint_services` in `platform/terraform.tfvars`, not on a consumer. Before a
consumer with a custom `kb_bucket_name` applies, add its exact bucket ARN to the platform's
`kb_bucket_arn_patterns`. The companion's warning-only `shared_endpoint_extras` check reminds you
of these at plan time. A shared network is one network boundary for several companions: read SD-27
in [`SECURITY.md`](SECURITY.md) first. On a shared data-plane endpoint `auth_mode = "none"` is
refused, and Memory gains a per-companion Deny.

### Adopting an existing platform state (human-run, not performed by this kit)

The ported modules keep the original resource names and arguments and the module names `backend`
and `identity`, so a platform state built by the original shared-platform code can be adopted:

1. In the original platform directory, `terraform plan` shows no changes.
2. Back up that `terraform.tfstate` outside both repositories.
3. In `agentcore/terraform/platform/`, create `terraform.tfvars` with the same `state_bucket_name`,
   `lock_table_name`, `identity_name_prefix` and `apps` as the original platform. This port has no
   default for `apps` or `aws_region`, so write both explicitly; when the original tfvars omitted
   one, copy the original `variables.tf` default. Leave `enable_private_network = false` and
   `enable_resolver_inbound_endpoint = false`.
4. Put the state where this directory reads it: copy the backup to `platform/terraform.tfstate`
   (gitignored), or migrate the original state to `platform/terraform.tfstate` in the bucket and use
   the same `backend.tf` here.
5. Run `terraform init`. `platform/versions.tf` requires hashicorp/aws `~> 6.68` (the lock file is
   local and gitignored), so a plan never uses an older provider than the 6.68.0 that last wrote the
   state; check `terraform version` in the original directory, and add `-upgrade` only to move to a
   newer one.
6. `terraform plan` must report "3 to add, 2 to change, 0 to destroy". The three creates are the
   3.2.2 hardening additions in `module.backend`: `aws_s3_bucket_lifecycle_configuration.state_logs`
   and `aws_s3_bucket_notification.state` / `.state_logs`. The two in-place updates are the 3.2.2
   access-log delivery fix: `aws_s3_bucket_server_side_encryption_configuration.state_logs` (SSE-KMS
   to SSE-S3) and `aws_s3_bucket_policy.state_logs_tls_only` (adds the `logging.s3.amazonaws.com`
   write grant). The new lifecycle **expires existing access logs** in the adopted log bucket once
   they are older than 365 days, and noncurrent log versions after 30 days; copy any logs you must
   keep longer before you apply. New outputs under "Changes to Outputs" are expected. Any other
   create, update or destroy means STOP: the inputs differ.
7. Stop using the original platform directory. Two directories must never manage one state.
   Rollback is restoring the backup.

### Upgrading to 3.4.1 (browser sign-in for the bridge and `diagram-fetch`)

3.4.1 changes only the local front-door helpers: no image, Terraform or AWS change, no apply.
The stdio bridge and `./run.sh diagram-fetch` now sign in through the browser by default
(OAuth 2.0 authorization code + PKCE, `GAC_AUTH_FLOW=pkce`) with the stack's existing public PKCE
client. Developers need only non-secret values: no client secret, password or AWS credentials.

- **What lands where.** A direct OAuth MCP client (Kiro's native `oauth` entry) gets the gated
  text and a diagram manifest only; the bridge (and `diagram-fetch`) lands the files. PKCE is the
  developer default; the password flow (`GAC_AUTH_FLOW=password`) is for the test harness
  (`scripts/e2e_deployed.py`, which sets it itself), `gateway_token.py`, the probe and automation.
- **Values to hand developers** (all from `terraform output`, none secret): `gateway_url` or
  `orchestrator_runtime_arn` and the Region; `cognito_oauth_frontdoor_client_id` as
  `GAC_COGNITO_CLIENT_ID`; `cognito_oauth_scope` with `openid` as `GAC_COGNITO_SCOPES`; and
  `GAC_COGNITO_DOMAIN`: `cognito_hosted_ui_domain` on a stack that owns its pool, or
  `https://<platform output shared_user_pool_domain>.auth.<region>.amazoncognito.com` on a shared
  pool (known issue: the companion's `cognito_hosted_ui_domain` output is valid only when it owns
  the pool). Setup: [`FRONT-DOORS.md`](FRONT-DOORS.md), "Bridge sign-in".
- **Breaking for a password-flow bridge config.** A bridge or `diagram-fetch` environment that
  still sets `GAC_COGNITO_CLIENT_SECRET` now fails with "GAC_AUTH_FLOW=pkce uses the public PKCE
  client and no client secret". Move developers to the PKCE values, or add
  `GAC_AUTH_FLOW=password` to keep the old flow (automation only).
- **Allow-lists.** The gateway and the orchestrator runtime authorizers already allow the PKCE
  client while `enable_oauth_frontdoor_client = true` (the default), so both bridge upstreams
  accept its token; the `cognito:groups` entitlement check applies unchanged.
- **Check.** From a network that reaches the endpoints: `./run.sh diagram-fetch "draw our
  private network path"` opens the browser once and saves the files (exit 0).

### Upgrading to 3.4.0 (natural-language diagrams and entitlement)

3.4.0 changes the runtime images (natural-language diagrams, multi-view answers), the MCP tool
surface (`draw_diagram` gains `planner` and a new description), the authorizers (an entitlement
claim rule), the runtime role (the explicit deny now applies in both modes), the identity
interceptor (a group re-check and its own KMS key) and adds an automatic gateway re-sync. Read
"Entitlement group" below first: from 3.4.0 a user who is not in the companion's group is denied.
In this order:

1. **Front-door helpers.** On every workstation that runs the bridge, `diagram-fetch`, the gate
   MCP server or the E2E harness: `pip install -r requirements-frontdoor.txt` (Python 3.10 or
   later; exact pins). A missing package prints one line, for example `Not fetched: mcp is not
   installed; run: pip install -r requirements-frontdoor.txt`, and exits 2.
2. **Entitlement group.** Make sure `<name_prefix>-users` exists and holds every user who should
   keep access (see "Entitlement group"). On a shared pool the companion never creates it.
3. **Test users for the deny-first E2E** (human-run Cognito writes on the pool; see "Deny-first
   E2E" below).
4. Stage the icons zip as before and **set a new `image_tag`** (ECR tags are immutable).
5. A human runs `terraform plan -out <plan>`, reviews it with `scripts/plan_review.py` (see
   "Review a saved plan"), then `terraform apply <plan>`.
6. Run the deny-first E2E, then the full E2E (`scripts/e2e_deployed.py`).
7. `python3 frontdoor/mcp_bridge.py --check-gateway-tools` lists `gac___draw_diagram`.

Expected plan entries (any other create, update or destroy: stop and explain it, or do not
apply):

- both image build `null_resource`s replaced; both runtimes updated in place to the new image URI
  (a runtime **replace** changes its ARN: stop-ship);
- the gateway's and the orchestrator's `custom_jwt_authorizer` gain a `custom_claim` (in-place
  update expected); the orchestrator `allowed_clients` drops the M2M client; the MCP runtime
  `allowed_clients` becomes the gateway's M2M client only when the gateway uses OAuth outbound (a
  gateway **replace** is stop-ship);
- the interceptor Lambda gains `GAC_REQUIRED_GROUP` and `GAC_REQUIRED_GROUP_CLAIM` and a
  customer managed key for its environment: `aws_kms_key.interceptor`, `aws_kms_alias.interceptor`
  and `aws_iam_role_policy.interceptor_env_key` created, the function and
  `aws_iam_policy.interceptor_boundary` updated in place;
- the runtime role's inline policy (the explicit deny is now unconditional and lists 23 actions);
- `terraform_data.gateway_tool_sync` created (it runs after the runtime update);
- on the consume path `data.aws_cognito_user_groups.entitlement` is read and **no**
  `aws_cognito_user_group` is planned; on the own path one `aws_cognito_user_group.entitlement`
  is created in the owned pool.

UNVERIFIED in AWS: 3.4.0 has been verified locally only (unit tests, mocked `terraform test`, the
security gate). The key-policy conditions of the interceptor key, the in-place versus replace
behaviour of the claim change, the gateway's limit on a multi-view tool result and the
authorizer's denial bodies are proven only by the first apply and the E2E. Record the result in
"Sandbox validation record".

#### Entitlement group

With a JWT authorizer (`auth_mode = "cognito"` or `"entraid"`) and the default
`require_entitlement_group = true`, the gateway and the orchestrator runtime accept a token only
when its `cognito:groups` claim (`entitlement_claim_name`; set `"groups"` or `"roles"` for Entra
ID, untested live) contains the companion's group, `entitlement_group_name` (default
`<name_prefix>-users`). The identity interceptor re-checks the same claim and answers HTTP 403
"GAC: not entitled" when it is missing (fail closed). The M2M client is no longer allowed on the
orchestrator. `require_entitlement_group = false` is refused in public mode and allowed, as a
weaker posture, in private mode.

- **Users who keep access:** members of `<name_prefix>-users` keep access after the apply. Users
  who are not members are denied until the pool's owner adds them. Groups are read when a token is
  minted, so a newly added user signs in again.
- **Shared pool (consume path, `manage_identity_pool = false`):** the companion never creates the
  group. The plan reads the pool's groups (`cognito-idp:ListGroups`, read-only) and fails with
  "entitlement group <name> not found in the shared user pool; the pool's owner creates it" when
  it is missing. The pool's owner creates it, preferably through the platform stack's `apps` map
  (`agentcore/terraform/platform/README.md`), or by hand:
  `aws cognito-idp create-group --user-pool-id <pool> --group-name <prefix>-users`.
  `manage_entitlement_group = true` on a shared pool is refused.
- **Plan-time check opt-out:** a planning identity without `cognito-idp:ListGroups` sets
  `check_entitlement_group_exists = false`; the group must then exist before the first user test.
- **Own pool (`manage_identity_pool = true`):** the companion creates `<name_prefix>-users` in its
  pool. With `manage_entitlement_group = false` (a team that manages groups elsewhere) it creates
  no group and runs no existence check; create the group before the first user test.
- **Adding a user** is a human-run write by the pool's owner; no agent runs it:
  `aws cognito-idp admin-add-user-to-group --user-pool-id <pool> --username <user> --group-name <prefix>-users`.

#### Gateway tool re-sync

A gateway target syncs its tool list only when it is created, so a new MCP runtime image used to
leave the gateway serving the old list ("Unknown tool: gac___draw_diagram"). From 3.4.0
`terraform_data.gateway_tool_sync` runs `agentcore/terraform/scripts/sync_gateway_target.sh`
inside the human-run apply whenever the MCP runtime image, its version or the tool surface
changes: it waits while authorization is pending, sleeps `gateway_sync_settle_seconds` (default
15, 0 to 120; a heuristic for the new version to settle, not an AWS figure), runs one
`synchronize-gateway-targets` and waits for `READY` (at most 300 s). The engine role is denied
`SynchronizeGatewayTargets` in both modes, so only the deployer's apply can re-sync.

- **Opt-out:** `sync_gateway_tools = false`.
- **Older AWS CLI:** a CLI without `bedrock-agentcore-control synchronize-gateway-targets` fails
  the step with "AWS CLI too old". Upgrade the CLI, or set `sync_gateway_tools = false` and run
  after the apply:
  `aws bedrock-agentcore-control synchronize-gateway-targets --gateway-identifier <gateway-id> --target-id-list <target-id>`
  (or the boto3 equivalent, `boto3.client("bedrock-agentcore-control").synchronize_gateway_targets(gatewayIdentifier=..., targetIdList=[...])`).
- **Check:** `python3 frontdoor/mcp_bridge.py --check-gateway-tools` prints `OK: gac___draw_diagram`.

#### Opt-in Bedrock diagram planner

The diagram specialist is deterministic by default. `diagram_planner = "bedrock"` adds one
Converse call per non-preset request, in which the model only selects fact ids from the
catalogue (it never adds a node or an edge); any error falls back to the deterministic planner
and the footer names the fallback code. It needs:

- `diagram_planner_model_id`: a Bedrock model or inference-profile id that supports Converse
  tool use (a model without tool use fails every call and falls back);
- a guardrail: `create_bedrock_guardrail = true` or an existing `guardrail_id`;
- in private mode, the bedrock-runtime endpoint: `enable_bedrock_runtime_endpoint = true` or
  `existing_interface_endpoint_ids["bedrock-runtime"]`;
- for a cross-Region inference profile, `diagram_planner_model_regions` (the Regions it routes to).

The plan fails with a message naming the missing variable. IAM: the runtime role gains
`DiagramPlannerGuardrail` (`bedrock:ApplyGuardrail` on that guardrail) and, with Regions listed,
`DiagramPlannerCrossRegionModels`; model invocation uses the existing `BedrockModelInvocation`
statement. Bounds: at most 1024 output tokens, the planner wall time is about `timeout_seconds`
(per-read bound; 3 to 15, default 15) and there is no SDK retry; a breaker opens after 3 failures
for 300 s. A caller can only downgrade the planner (`--planner deterministic`, the tool's
`planner` argument).

#### Public-stack upgrade

A public stack whose tfvars do not set `network_access` would plan a move to private mode (the
default since 3.0.0): a VPC-mode runtime change that may replace the runtimes. Keep it public
with command-line overrides, without editing the tfvars file:

```bash
terraform plan -out <plan> \
  -var network_access=public -var acknowledge_public_endpoint=true \
  -var image_tag=<new-tag> -var diagram_icons_sha256=<sha256-of-your-staged-zip>
```

Expect the 3.4.0 entries above and the runtimes' `network_mode` staying `PUBLIC`; a change to
`network_mode`, a runtime replace or `has moved to` lines mean stop and investigate.

#### Review a saved plan

```bash
terraform show -json <plan> > <plan>.json                 # local, read-only
python3 scripts/plan_review.py --forbid-cognito-groups <plan>.json
```

It lists every resource change by action (update lines name the changed attributes, never their
values) and exits 1 when a protected resource (Cognito pool, clients, domain, resource server,
groups, KB buckets and their public access blocks, KMS keys and aliases, ECR repositories, Memory,
VPC, subnets, endpoints, the gateway, its target, the runtimes, the OAuth credential provider) is
deleted or replaced. `--forbid-cognito-groups` also fails on any `aws_cognito_user_group` change:
use it on the consume path, where the companion must not touch the shared pool's groups. Exit 2
means the file is not a plan. Treat the plan files as sensitive and never commit them.

#### Deny-first E2E (`scripts/e2e_deployed.py`)

Human-run, read-only, after the apply. Before it, the pool's owner creates two test users (human
Cognito writes; `read -rs` keeps the password out of shell history and the terminal):

```bash
# U0: in NO group (required)
aws cognito-idp admin-create-user --user-pool-id <pool> --username <u0> --message-action SUPPRESS
read -rs P && aws cognito-idp admin-set-user-password --user-pool-id <pool> --username <u0> \
  --password "$P" --permanent; unset P
# U1 (optional): only in a test-only group that no companion's claim rule names
aws cognito-idp create-group --user-pool-id <pool> --group-name e2e-no-app
aws cognito-idp admin-create-user --user-pool-id <pool> --username <u1> --message-action SUPPRESS
read -rs P && aws cognito-idp admin-set-user-password --user-pool-id <pool> --username <u1> \
  --password "$P" --permanent; unset P
aws cognito-idp admin-add-user-to-group --user-pool-id <pool> --username <u1> --group-name e2e-no-app
```

Never put U1 in another companion's group: that would grant it real access there. Then:

```bash
terraform -chdir=agentcore/terraform output -json > /path/outside/the/repo/outputs.json
export GAC_COGNITO_USERNAME=<entitled user> GAC_E2E_UNENTITLED_USERNAME=<u0>
export GAC_E2E_OTHERGROUP_USERNAME=<u1>          # optional
export GAC_COGNITO_CLIENT_SECRET=...              # the front-door client secret
export GAC_E2E_M2M_CLIENT_SECRET=...              # public stack: the M2M client secret (D4a)
python3 scripts/e2e_deployed.py --stack public --outputs /path/outside/the/repo/outputs.json --deny-only
python3 scripts/e2e_deployed.py --stack public --outputs /path/outside/the/repo/outputs.json
python3 scripts/e2e_deployed.py --stack private --outputs <private outputs.json> --probe <function> \
  --compare outputs/e2e/public-<utc>/e2e-public.json
unset GAC_COGNITO_CLIENT_SECRET GAC_E2E_M2M_CLIENT_SECRET
```

It asks once per user for the password (no echo) and never prints or writes a token, password or
secret. Deny tests run first: anonymous, a malformed token, U0, U1, the M2M client, another
flavor's client (`--other-client-id` with `GAC_E2E_OTHER_CLIENT_SECRET`, optional) and, on the
public stack, a user token sent straight to the MCP runtime. On the private stack it runs tests
T2, T4a, T5b and T9 from outside and D1 to D3b through the probe (`{"anonymous": true}` for D1);
D4a is `not-run` there (no M2M client in private mode). Each deny must be HTTP 401 or 403 with an
identifiable denial (a `www-authenticate` header, an authorizer marker or "GAC: not entitled").
**Any allowed deny test is stop-ship: exit 5, and nothing else runs.** The full run then calls
`ask_companion` and `companion_kb` and draws every example-KB golden prompt through every door
(bridge with gateway and runtime upstreams, `diagram-fetch` on both, the probe on the private
stack; `--doors` picks a subset, for example `--doors probe` without a network path), with the
planner forced to `deterministic` on the bridge doors, and checks that every door gives the same
files. `--compare` checks a second stack's run (same catalogue: drawio and SVG equal; a different
catalogue is reported as "KB differs"). Results: `outputs/e2e/<stack>-<utc>/e2e-<stack>.json` and
`.md` (gitignored). Exit codes: 0 passed, 1 a test or parity failed, 2 configuration, 5 stop-ship.
The authorizer's exact denial bodies are unverified: record them from the first deny run.

Afterwards the pool's owner deletes the test users and group:

```bash
aws cognito-idp admin-delete-user --user-pool-id <pool> --username <u0>
aws cognito-idp admin-delete-user --user-pool-id <pool> --username <u1>
aws cognito-idp delete-group --user-pool-id <pool> --group-name e2e-no-app
```

### Upgrading from 3.3.0 (the 3.3.1 hardening release)

3.3.1 changes the runtime images (new digest-pinned base, see "Base image" below) and adds no AWS
resource, no IAM statement and no MCP tool. The Terraform edits are comments only (KICS markers).
In this order:
1. Stage the AWS Architecture Icons zip again if you use the diagram specialist (unchanged).
2. **Set a new `image_tag`.**
3. A human runs `terraform plan` and `terraform apply`. Expected plan: both image build
   `null_resource`s are replaced (their triggers hash the Dockerfiles and requirements files) and
   both runtimes update in place to the new image URI. Any other create, update or destroy means
   STOP and check the inputs.
4. No gateway target sync is needed (the tool list is unchanged).
### Upgrading from 3.2.x (the 3.3.0 diagram specialist)
3.3.0 adds no AWS resource and no IAM statement. The runtimes gain the diagram specialist and the
MCP runtime gains the `draw_diagram` tool. In this order:
1. Stage the AWS Architecture Icons zip (see "Stage the AWS Architecture Icons" below), or skip it
   and accept that the diagram specialist declines in this image.
2. **Set a new `image_tag`.** ECR tags are immutable, so a rebuild under the old tag fails at push
   and the runtimes would not pick up a same-tag image anyway.
3. A human runs `terraform plan` and `terraform apply`. Staging or replacing the zip, or editing the
   diagram code, changes the build triggers; other engine edits under `agents/` or `knowledge/` are
   not hashed by the triggers (a pre-existing gap), so they also need a new `image_tag` or
   `-replace` on the build resources.
4. A human synchronizes the gateway target so the gateway lists the new tool (target creation runs
   one tool sync; an update of the runtime does not). After the runtimes are updated:
   ```bash
   aws bedrock-agentcore-control list-gateway-targets --gateway-identifier <gateway-id>
   aws bedrock-agentcore-control synchronize-gateway-targets \
     --gateway-identifier <gateway-id> --target-id-list <target-id-of-gac>
   python3 frontdoor/mcp_bridge.py --check-gateway-tools   # expects gac___draw_diagram
   ```
   The CLI command names derive from the `SynchronizeGatewayTargets` API; confirm them with
   `aws bedrock-agentcore-control help` for your CLI version. The kit never runs these (Tenet 1).
   New deployments need no step: target creation runs the sync.
5. Optionally set `diagram_icons_sha256` so later builds refuse any other zip.

### Upgrading a companion created before 3.2.0

Run `terraform init` once (it installs the local `modules/private-network`). An existing
`create_vpc = true` or existing-VPC deployment then plans `has moved to` lines for the network
resources (`moved.tf`) and no replacement. A public-mode stack plans no network change; its tfvars
still need `network_access = "public"` and `acknowledge_public_endpoint = true` (required since
3.0.0).

## Configure

```bash
cd agentcore/terraform
cp terraform.tfvars.example terraform.tfvars      # gitignored — fill in for your engagement
```

Key inputs (all documented in `variables.tf`):

| Input | Purpose |
|---|---|
| `aws_region` / `name_prefix` | Where to deploy; resource-name prefix |
| `bedrock_model_id` | Inference-profile id valid in `aws_region` |
| `auth_mode` | `cognito` (create IdP) / `entraid` (existing tenant) / `none` (test only) |
| `deploy_gateway` | Create the gateway front door (requires cognito/entraid) |
| `enable_identity_interceptor` | Verified per-builder identity propagation (ADR-0001) |
| `enable_memory` | Provision AgentCore Memory (opt-in ready infrastructure) |
| `create_bedrock_guardrail` / `pii_backend` | Managed guardrail defense in depth |
| `cognito_mfa_mode` / `cognito_advanced_security_mode` | Cognito hardening |
| `network_access` | `private` (default: VPC mode + PrivateLink only) or `public` (demo opt-in) |
| `create_vpc` or `vpc_id` / `private_subnet_ids` / `private_route_table_ids` | Private mode network inputs |
| `private_client_cidrs` | Developer/IDE networks allowed to reach the gateway and data-plane endpoints |
| `knowledge_admin_principal_arns` | Principals exempt from the KB bucket and ECR VPC-endpoint-only denies |
| `trace_tool_content` | `false` (default) disables tool content in traces in private mode |
| `manage_identity_pool` / `existing_user_pool_id` / `existing_oidc_issuer` / `engine_resource_server_identifier` | Consume a shared pool (Path A); the identifier must be unique in the pool |
| `manage_existing_endpoint_ingress` / `existing_client_endpoint_security_group_ids` | Shared-network consumers |
| `enable_resolver_inbound_endpoint` / `resolver_inbound_client_cidrs` | Optional Route 53 Resolver inbound endpoint for DX/VPN DNS |
| `enable_private_access_probe` | Optional test stand-in for a DX/VPN workstation (disable after testing) |
| `diagram_icons_sha256` | Optional SHA-256 of your staged AWS Architecture Icons zip; the build fails on a mismatch |
| `diagram_enabled` | `true` (default); `false` sets `GAC_DIAGRAM_ENABLED=false` so the diagram specialist declines |
| `diagram_planner` / `diagram_planner_model_id` / `diagram_planner_model_regions` | Opt-in Bedrock diagram planner (default `deterministic`); see "Opt-in Bedrock diagram planner" |
| `sync_gateway_tools` / `gateway_sync_settle_seconds` | Automatic gateway tool re-sync inside the apply (default on, 15 s settle) |
| `require_entitlement_group` / `entitlement_group_name` / `manage_entitlement_group` / `check_entitlement_group_exists` / `entitlement_claim_name` | Entitlement claim rule (default on, `<name_prefix>-users`); see "Entitlement group" |

The full private-mode variable table is in
[`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) "Network access".

> **⚠️ Public mode is a demo opt-in.** `network_access = "public"` needs
> `acknowledge_public_endpoint = true`. The gateway and runtimes are then reachable from the
> internet and protected only by the JWT authorizer: anyone holding a valid token can query your
> proprietary knowledge from anywhere. Use it only in a sandbox account. `auth_mode = "none"` is
> refused in public mode.

## Stage the AWS Architecture Icons

The diagram specialist draws with the official AWS Architecture Icons. They are never in this
repository and are never pushed by the kit. To include them in your images:

1. Download the AWS Architecture Icons **Asset Package** zip from
   <https://aws.amazon.com/architecture/icons/> yourself. Read the terms on that page; staging the
   package means you accept them for your use (`THIRD-PARTY-LICENSES.md`).
2. Copy **exactly one** zip, under any filename, into `agentcore/diagram-icons/` (gitignored except
   its README). Two or more zips stop the build: `build_and_push.sh` (the Terraform build path)
   prints `ERROR: more than one .zip in agentcore/diagram-icons/` before Docker starts, and a
   manual `docker build` fails in the catalog stage with `FATAL: more than one .zip in
   agentcore/diagram-icons/; keep exactly one ...`.
3. Optional: set `diagram_icons_sha256 = "<sha256-hex>"` (or `GAC_DIAGRAM_ICONS_SHA256` for a manual
   `build_and_push.sh`); a different zip then fails with `FATAL: icons zip sha256 mismatch`.
4. Set a new `image_tag` and rebuild (a human runs `terraform apply`, or the build script).

The discarded `diagram-assets` build stage reads the zip member by member (no extraction), keeps
only the 48 px service and resource icons and the 32 px group icons, sanitizes each SVG with an
allowlist, caps each at 16 KiB and writes a catalog with a SHA-256 per icon; the runtime verifies
the catalog and each icon it embeds. With no zip staged the image still builds (the build log
prints `WARNING: AWS Architecture Icons not staged`), the specialist stays registered and every
draw request returns "the AWS Architecture Icons were not staged when this image was built".
Generated diagrams embed sanitized icon artwork; they are written only to `outputs/` or the
developer's workspace and are never committed to the kit.

Diagrams come from knowledge-base diagram facts, not from live deployment state. The shipped
example describes the kit's default private topology; a fork that deploys differently (for
example public mode) replaces those facts in its own Tier-B KB (`docs/ARCHITECTURE.md`, "Describe
your topology for diagrams").

**Image impact (measured on local `linux/arm64` builds of 3.3.1, 2026-11-02, and again on the 2026-11-03 re-pin, same sizes):** orchestrator
282 MB without icons and 287 MB with icons, MCP runtime 294 MB and 299 MB, against 398 to 415 MB
for 3.3.0 on `python:3.12-slim`. The images hold the venv (no pip), the `resvg-py` rasterizer, two
pinned DejaVu fonts and about 5 MB of sanitized icons. A diagram request for the largest shipped
topic takes about 0.4 s end to end against a local orchestrator container (0.37 to 0.39 s
measured: layout, emitters and the 2x PNG); one raster runs per process.

### Base image

Both images build on Chainguard Python (Wolfi), pinned by multi-arch index digest, with the tag in
a comment above each `FROM`: `cgr.dev/chainguard/python:latest-dev` for the two discarded build
stages (shell, `apk`, compiler) and `cgr.dev/chainguard/python:latest` for the runtime stage (no
shell, no package manager, non-root uid 65532, `ENTRYPOINT []` so the `CMD` runs as written). The
`runtime-deps` stage installs the requirements into `/app/venv` and then uninstalls pip, so no pip
ships in either image; the fonts are pinned (`ttf-dejavu=2.37-r9`). Code, venv and icon catalog
are root-owned; only `/app/engine/.audit`, `.feedback` and `.plans` are writable by the runtime
user. `--pull` on a digest-pinned `FROM` only re-checks that digest, so the base layer is the same
in every build until you refresh the pins. The Python packages are not: only the direct
requirements are pinned, and their transitive dependencies (about 100 packages) resolve at build
time. The image build `null_resource`s rebuild both images on `terraform apply`, so the image you
deploy is a new build, not the one scanned for this release. A hashed `pip --require-hashes` lock
per image is a planned follow-up.

Scan state, measured on 2026-11-03 with the standard gate on the images built that day from the
2026-11-03 base pin (Python 3.15.2; Trivy DB of 2026-11-03): Trivy reports 0 findings at every
severity (fixable and unfixed) in all four images (both faces, with and without icons). The first
3.3.1 pin (2026-11-02) showed 8 fixable Trivy findings after that DB update, which is why the base
was re-pinned. The result applies to those builds only. Before you apply, run
`./scripts/security_gate.sh` (add `--require-icons` if you staged the icons zip) on the commit you
will deploy, or review the ECR scan-on-push findings for the pushed tag, and do not deploy a build
with a finding outside the approved residuals. Grype
reports one Low CPython finding on the base's `python-3.15` package (CVE-2026-103995, no released
CPython fix), recorded with evidence and an expiry date as the accepted residual R2 in
`docs/security/scan-results.md` and `security/accepted-residuals.yaml`. Since 2026-11-05 Grype
also reports CVE-2026-119586 (High) in `sqlite-libs` 3.54.1-r0 on the same base: a flaw in the
SQLite command-line shell, which the images do not ship (library only, no `sqlite3` binary), with
no fix in Wolfi yet; it is the accepted residual R4 (approved 2026-11-05, review by 2026-11-30),
and it affects the 3.3.1 and 3.4.0 images alike. Re-pin the base once a fixed `sqlite-libs`
ships. Scanner databases change
daily, so a clean result can turn into a failing one without a code change: re-run the gate
before every deploy.

**Refresh the base (human-run).**

1. Pull both tags at the same time, so they carry the same Python minor (the venv's home is
   `/usr/bin` in the runtime stage):
   `docker pull --platform linux/arm64 cgr.dev/chainguard/python:latest` and
   `docker pull --platform linux/arm64 cgr.dev/chainguard/python:latest-dev`.
2. Read the multi-arch index digests:
   `docker buildx imagetools inspect cgr.dev/chainguard/python:latest --format '{{json .Manifest.Digest}}'`
   (and the same for `latest-dev`).
3. Replace the digests and the "refreshed" date in both Dockerfiles together (a static test
   asserts that both files use the same `FROM` lines). If the Python minor changed, check the
   `ttf-dejavu` pin with `apk search -x ttf-dejavu` in `latest-dev` and update the "verified on"
   note in both runtime `requirements.txt` files.
4. Run `./scripts/security_gate.sh --require-icons`. Remove an accepted residual the new base
   fixes; any new finding fails the gate until it is fixed or approved (see
   `docs/security/scan-results.md`).
5. Rebuild and test with a new `image_tag` (ECR tags are immutable).

**Security gate.** `./scripts/security_gate.sh` is the standard pre-release gate: Checkov,
Bandit, pip-audit, detect-secrets, KICS, Trivy and Grype with pinned versions, failing on any
finding that is not an inline, justified suppression or an approved residual. It builds images
locally only and never pushes. Results and every suppression: `docs/security/scan-results.md`.
The gate also fails once an accepted residual passes its `review_by` date, in CI too
(`security-gate-static`). A fork that keeps the 3.3.1 base must re-review the residuals (and
record the new `review_by`) or re-pin the base before that date, or its pipeline fails.

## Deploy (the sequence a human runs)

```bash
export AWS_PROFILE=<your-profile>
terraform init
terraform plan -out tf.plan      # review the plan — you decide
terraform apply tf.plan          # builds+pushes both images, then creates the stack
```

Notes:
- `apply` runs the build/push helper for both arm64 images before creating the runtimes.
- ECR tags are **immutable** by default — bump `image_tag` for each redeploy (e.g. `v0-1-0` →
  `v0-1-1`); re-pushing the same tag is rejected by design.

## Verify (end-to-end)

1. **Runtimes READY:**
   ```bash
   for r in $(terraform output -raw orchestrator_runtime_arn | sed 's#.*/##') \
            $(terraform output -raw mcp_runtime_arn | sed 's#.*/##'); do
     aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$r" \
       --region <REGION> --query '[agentRuntimeName,status]' --output text
   done
   ```
2. **MCP runtime serving (not crash-looping):** check
   `/aws/bedrock-agentcore/runtimes/<mcp-runtime>-DEFAULT` for `POST /mcp ... 200 OK`.
3. **Gateway target created:** a `FAILED` target means the MCP runtime errored on boot — read its
   CloudWatch logs (a missing/incompatible dependency is the usual cause).
4. **Governed invocation:** connect an IDE (see [`FRONT-DOORS.md`](FRONT-DOORS.md)) and ask a
   question that hits a known KB marker; confirm a **gated** answer with the governance footer.
5. **Local integrity:** `./run.sh status` reports the governance integrity check passing.

**MCP health check note.** The AgentCore Runtime health check and READY state can only be
confirmed by deploying. The in-process test `tests/test_mcp_runtime_stateless.py` proves the
protocol behaviour (stateless, accepts a platform `Mcp-Session-Id`, `0.0.0.0:8000/mcp`), not the
platform health check (post-deploy test 6). AgentCore supports both stateless and stateful
streamable-HTTP MCP servers; stateless is this kit's choice
([MCP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html)).

## Before you distribute anything (private mode)

During a first private-mode apply each runtime is READY with the knowledge image for the seconds
between its creation and its resource policy (the provider accepts no inline policy; SD-19 in
[`SECURITY.md`](SECURITY.md)). Front-door files in `generated/` are written only after every
private-mode policy is attached. **Post-deploy tests 2 and 4a are a hard gate:** do not hand out
tokens, the generated front-door files, or onboarding until both return the resource-policy
denial. A `200` from outside the approved networks is a stop-ship: fix it before anything else.

## Post-deploy tests (private mode)

Run these in a sandbox account and record each HTTP status and response body. Tests 2, 3, 4a, 5a,
5c, 6, 8, 12 and 16 were validated in a sandbox account on 2026-10-31 against kit 3.2.1; test 5b was
validated for the SigV4 denial only (where it ran was not recorded) and test 9 for the outside
denial only (see
[Sandbox validation record](#sandbox-validation-record-2026-10-31)). The kit 3.2.2 changes (VPC Flow
Logs, probe CMK and DLQ, backend-bootstrap additions, the OpenTelemetry bump in both images) are
UNVERIFIED in AWS. Every other test, and every result in your own account, stays UNVERIFIED until
you run it. Verify denial first. Export `GAC_GATEWAY_URL` and `GAC_GATEWAY_TOKEN` yourself; the probe is
`terraform output -raw private_access_test_command` (the token is never interpolated into it).

1. **Mode and network.** `terraform output network_access` returns `private`.
   `aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id>` for both runtimes
   returns `status READY` and `networkConfiguration.networkMode VPC` with the expected subnets and
   SG ([CLI](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/get-agent-runtime.html)).
   The `orchestrator_require_service_s3_endpoint` and `mcp_runtime_require_service_s3_endpoint`
   outputs are `false`.
2. **HARD GATE. Internet denial (gateway).** From a host outside every approved network, with a
   valid token, the probe returns `403`. The body must identify an authorization / resource-policy
   denial, not a 401 JWT error and not a DNS failure. A `401` means the JWT was rejected first, so
   the policy is untested: re-mint and retry. A `200` is a stop-ship.
3. **In-network success (gateway).** From a host in `private_client_cidrs` (VPN client, in-VPC
   desktop, or CloudShell VPC environment), `nslookup <gateway host>` returns private IPs in the VPC
   CIDR and the probe returns `200`. `tools/list` returns `gac___ask_companion` and
   `gac___companion_kb`; a `tools/call gac___companion_kb` returns a gated answer with the
   governance footer.
4. **Bridge.** (a) **HARD GATE, with test 2:** from outside, `frontdoor/mcp_bridge.py` invoking the
   orchestrator with a valid token gets HTTP 403 with a resource-policy denial body (not 401). From
   inside, it succeeds. (b) Optional: with `GAC_AGENTCORE_ENDPOINT` set to the endpoint-specific
   DNS name (`agentcore_dataplane_vpce_dns_names` output), the TLS handshake succeeds (UNVERIFIED
   for AgentCore until this test).
5. **MCP runtime is gateway-only.** (a) A direct HTTPS call to the MCP runtime invocations URL with
   the front-door JWT is rejected (the runtime has no JWT authorizer in private mode). (b) A SigV4
   `aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn <mcp_runtime_arn> ...` with an
   admin role from inside the VPC returns AccessDenied with an explicit deny in the resource-based
   policy ([CLI](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/invoke-agent-runtime.html)).
   (c) The gateway target status is `READY`
   (`aws bedrock-agentcore-control get-gateway-target --gateway-identifier <id> --target-id <id>`,
   [CLI](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/get-gateway-target.html)),
   which shows the gateway role was assumed with the narrowed trust pattern and synced tools over
   SigV4. If it fails with an `sts:AssumeRole` AccessDenied naming the gateway role, revert only the
   gateway trust `aws:SourceArn` value in `gateway.tf` to `arn:aws:bedrock-agentcore:<region>:<account-id>:*`
   (keep `aws:SourceAccount`), re-apply, re-run 5c, and record the observed gateway ARN format.
6. **MCP health check.** The MCP runtime is `READY`, and its log group shows `POST /mcp` 200
   responses after test 3. This is the only confirmation of the platform health check.
7. **KB bucket.** (a) From a workstation with a non-exempt role,
   `aws s3 cp s3://<kb>/<kb_prefix>/curated/overlay.json -` returns AccessDenied. (b) From an exempt
   curator role (listed in `knowledge_admin_principal_arns`, with the curator identity policy) outside
   the VPC, both the read and an upload of a copy of the current overlay succeed. A KMS AccessDenied
   here means `KnowledgeAdminS3Use` or the identity policy is missing. (c) The runtime logs show
   `KB overlay applied` (not `KB overlay unavailable`).
8. **No internet egress, no tool content in traces.** The runtime logs show no connect timeouts to
   STS, monitoring or other services; if any appear, add the service to
   `extra_interface_endpoint_services` (never `bedrock-agentcore-control`, SD-21) and re-run test 1.
   The logs show no AccessDenied naming the VPC endpoint policy for a `bedrock-agentcore` action; if
   one appears, add that action to `dataplane_endpoint_extra_actions` and re-run. Then, read-only,
   query the spans for the test-3 window (`aws xray get-trace-summaries` +
   `aws xray batch-get-traces`, [CLI](https://docs.aws.amazon.com/cli/latest/reference/xray/get-trace-summaries.html),
   or a CloudWatch Logs Insights query on the spans log group,
   [Logs Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html))
   and confirm that no span carries `gen_ai.tool.call.arguments` or `gen_ai.tool.call.result`.
   Optionally, the VPC flow logs show only endpoint-IP and S3-prefix destinations for the runtime
   ENIs (with `create_vpc = true` the kit writes them to `/vpc/<prefix>-private/flow-logs`).
9. **Memory** (only with `enable_memory = true`). A SigV4 `aws bedrock-agentcore list-events` from
   outside the VPC returns an explicit deny, and from inside it is allowed
   ([CLI](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/list-events.html)).
10. **Endpoint SG.** From a host in the VPC but outside `private_client_cidrs` and the runtime SG, a
    TCP 443 connection to the gateway endpoint IP times out.
11. **Public regression** (a separate sandbox, `network_access = "public"` with the
    acknowledgement). Today's behaviour applies: the probe from the internet with a JWT returns 200,
    and no endpoints or resource policies exist. Record whether spans carry
    `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` (SD-24), and whether the first apply by
    a non-root principal with `seed_overlay_object = true` created `aws_s3_object.overlay_seed` or
    failed with a KMS AccessDenied on `GenerateDataKey` (SD-22).
12. **Policies attached** (run first, before test 2).
    `aws bedrock-agentcore-control get-resource-policy --resource-arn <arn>` for the gateway, the
    orchestrator and the MCP runtime (and Memory when enabled) returns the `resource_policies.tf`
    documents ([CLI](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/get-resource-policy.html)).
    `aws ecr get-repository-policy --repository-name <repo>` for both repositories shows
    `DenyPullNotViaEcrEndpointsOrAdmins`; `aws s3api get-bucket-policy --bucket <kb>` shows
    `DenyDataAccessNotViaS3Endpoint`. Read-only simulation:
    `aws iam simulate-principal-policy --policy-source-arn <runtime_execution_role_arn> --action-names bedrock-agentcore:PutResourcePolicy bedrock-agentcore:DeleteResourcePolicy bedrock-agentcore:CreateAgentRuntime bedrock-agentcore:UpdateAgentRuntime bedrock-agentcore:CreateAgentRuntimeEndpoint bedrock-agentcore:UpdateAgentRuntimeEndpoint bedrock-agentcore:CreateGateway bedrock-agentcore:UpdateGateway bedrock-agentcore:CreateGatewayTarget bedrock-agentcore:UpdateGatewayTarget bedrock-agentcore:UpdateMemory bedrock-agentcore:CreateHarness bedrock-agentcore:UpdateHarness bedrock-agentcore:CreateHarnessEndpoint bedrock-agentcore:UpdateHarnessEndpoint bedrock-agentcore:CreateCodeInterpreter bedrock-agentcore:CreateBrowser`
    returns `explicitDeny` for each of the 17 actions
    ([CLI](https://docs.aws.amazon.com/cli/latest/reference/iam/simulate-principal-policy.html)).
13. **ECR images.** (a) From a workstation outside the VPC, with a role that has identity-based ECR
    pull rights but is NOT in `knowledge_admin_principal_arns`, `docker pull <repo>:<tag>` (after
    `aws ecr get-login-password`) fails with an explicit-deny error; with a listed role it succeeds.
    (b) Both runtimes reach `READY` on first create under the policy, and after pushing a new tag and
    updating the runtime the new version reaches `READY`. If (b) fails with an ECR AccessDenied,
    first add `"aws:ViaAWSService": "false"` to the Deny's `BoolIfExists` in `ecr.tf` (and the
    matching static test), re-apply and re-run (b); only if it still fails, set
    `ecr_pull_restriction = false`. Record which step was needed and accept SD-18.
14. **Knowledge-admin plan warning.** Run `terraform plan` (the operator, in the sandbox) as a
    principal not matched by `knowledge_admin_principal_arns`: the `runner_is_knowledge_admin`
    warning appears. Then confirm the KB-bucket lockout recovery (Troubleshooting) with a second,
    unlisted principal.
15. **Teardown.** `terraform destroy` in private mode. If SG or subnet deletion fails with
    `DependencyViolation` because of lingering AgentCore ENIs (they can persist up to 8 hours,
    [agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)),
    re-run later. Record how long it took.
16. **Probe internal path** (only with `enable_private_access_probe = true`). Export
    `GAC_GATEWAY_TOKEN`, run the command from
    `terraform output -raw private_access_probe_invoke_command`, and record the result: `dns.ips`
    are private and inside the VPC CIDR; `initialize`, `tools/list` and `tools/call` return 2xx with
    `tool_listed: true`; `runtime.invoke` returns 2xx. The probe log group
    (`/aws/lambda/<prefix>-private-access-probe`) shows `step=` lines and no token. UNVERIFIED
    until you run it. Then disable the probe.

### Sandbox validation record (2026-10-31)

A private stack (`network_access = "private"`, `deploy_gateway = true`, `enable_memory = true`,
`enable_private_access_probe = true`, kit 3.2.1) was deployed and tested in a sandbox account.
Identifiers are replaced with placeholders; the VPC used the kit's default range `10.80.0.0/16`.

| Test | Where | Result |
|---|---|---|
| 12 Policies attached | operator, read-only | The four AgentCore resource policies (gateway, orchestrator, MCP runtime, Memory) match `resource_policies.tf`; `DenyPullNotViaEcrEndpointsOrAdmins` on both ECR repositories; `DenyDataAccessNotViaS3Endpoint` on the KB bucket; `simulate-principal-policy` on the runtime role returns `explicitDeny` for 17 of 17 actions |
| 2 Gateway, internet denial | laptop outside the VPC, valid Cognito token | The gateway hostname resolved to public AWS addresses. `initialize` returned HTTP 403 with JSON-RPC error `-32002` "Authorization error - Insufficient permissions" |
| 4a Bridge path, orchestrator | outside the VPC | HTTP 403: `InvokeAgentRuntime` on the orchestrator runtime refused "with an explicit deny in a resource-based policy" |
| 4a Bridge path, orchestrator | inside the VPC (probe `call_runtime`) | `bedrock-agentcore.<region>.amazonaws.com` resolved to `10.80.x.x` inside the VPC CIDR; HTTP 200 with `gated: true` and the governance footer |
| 5a MCP runtime with the JWT | outside the VPC | HTTP 403 "Authorization method mismatch" (the runtime is SigV4-only). This proves only that the JWT method is rejected |
| 5b MCP runtime with SigV4 | admin role; location not recorded (the test transcript does not show it; the release brief lists it with the outside-the-VPC calls) | AccessDenied with an explicit deny in the resource-based policy |
| 5c Gateway target | operator | `READY` |
| 3 Gateway, in-network | inside the VPC (probe) | The gateway hostname resolved to two `10.80.x.x` addresses in the VPC CIDR. `initialize` 200, `notifications/initialized` 202, `tools/list` 200 listing `gac___ask_companion` and `gac___companion_kb`, `tools/call gac___companion_kb` 200 with a sourced answer and the governance footer (verified zone, Tenets 1, 3, 4, 6, 11 and 12 PASS, gate status PASS) |
| 6 MCP health | MCP runtime log | `POST /mcp` 200 after test 3 |
| 8 Egress and traces | runtime logs, X-Ray | No connect timeouts and no AccessDenied in the runtime logs; no span carries `gen_ai.tool.call.arguments` or `gen_ai.tool.call.result` |
| 9 Memory | outside the VPC | `ListEvents` refused with an explicit deny in the resource-based policy. The inside result was not recorded |
| 16 Probe | probe Lambda | Private IPs inside the VPC CIDR, 2xx on every step, `tool_listed: true`, runtime invoke 200. The probe log group holds only `step=` lines and no token |

Not run in that sandbox: tests 1, 4b, 7, 10, 11, 13, 14 and 15, and the in-VPC variant of 9.
Re-run 5b and record where it ran. The first apply hit the IAM propagation race described in Troubleshooting ("Authorization error
when sending message"); kit 3.2.1 added the wait that fixes it.

**This record covers kit 3.2.1 only.** Kit 3.2.2 is UNVERIFIED in AWS: it adds VPC Flow Logs for a
created VPC, a CMK and a dead-letter queue on the probe (a new test-16 path; the KMS grant to the
boundary-limited probe role is untested), backend-bootstrap additions and the state access-log
delivery fix (SSE-S3 target plus the log-delivery grant; check that objects appear under
`state-access-logs/`), and the
`aws-opentelemetry-distro` 0.22.0 / `opentelemetry-api` 1.45.1 bump in both images. Follow-up before
relying on 3.2.2: rebuild both images, apply, re-enable the probe, and re-run tests 8, 12 and 16
(then disable the probe). The offline half of the ADOT pin-bump checklist below has been done for
0.22.0.

**Kit 3.3.0 is UNVERIFIED in AWS.** The diagram specialist has been verified locally only: the CLI,
the bridge relaying to the orchestrator image on `127.0.0.1`, and the bridge's gateway relay path
against the MCP runtime image on `127.0.0.1` without a bearer token. Not yet run against a deployed
stack: the rebuilt images on AgentCore Runtime, the gateway target sync, the gateway's handling of
a 270 to 370 KB tool result through the identity interceptor, and the Cognito-authenticated
gateway relay. Post-deploy check before relying on 3.3.0 (a human runs it; with the gateway
deployed):

```bash
python3 frontdoor/mcp_bridge.py --check-gateway-tools      # lists gac___draw_diagram
# Gateway path (gac___draw_diagram through the identity interceptor):
./run.sh diagram-fetch "draw our private network path"
# Runtime path (the bridge's default IDE upstream: GAC_RUNTIME_ARN, /invocations, Cognito JWT):
./run.sh diagram-fetch --upstream runtime --overwrite "draw our private network path"
# each run: expect "Saved locally:" with outputs/diagrams/private-request-path/ .drawio, .svg,
# .png and .report.json, each "(sha256 verified)"
```

Both commands use the bridge's own `draw_via_gateway` and `draw_via_runtime` functions and its
writer, so they cover the two upstreams the IDE bridge uses. They do not cover the IDE's MCP
stdio session itself; check that once from the IDE by calling `draw_diagram`.

Record the result here, as for 3.2.1.

**Kit 3.3.1 is UNVERIFIED in AWS.** It adds or removes no AWS resource and changes no IAM
statement; both image builder `null_resource`s are replaced and both runtimes update in place to
the new image URI. Both images change: Python
3.12 to 3.15, uid 10001 to 65532, `ENTRYPOINT []`, and root-owned code with only the engine's
`.audit`, `.feedback` and `.plans` directories writable. Verified locally on `linux/arm64`: both
images build with and without icons, report healthy, answer `/ping` and `/invocations`
(orchestrator) and `tools/list` (MCP runtime), render a PNG with resvg, and write
`.audit/trail.jsonl` as uid 65532. Not yet run on AgentCore Runtime. Post-deploy check before
relying on 3.3.1 (a human runs it):

1. Both runtimes are `READY` after the update, an orchestrator request returns 200 (test 3), and
   the MCP runtime log group shows `POST /mcp` 200 (test 6).
2. OpenTelemetry export reaches CloudWatch: spans for the test-3 window appear (test 8), and the
   runtime logs show no exporter or permission error at start-up.
3. The runtime still serves gated answers on the read-only code tree: a request through the
   runtime returns a gated answer with its governance footer, and the runtime logs show no
   start-up traceback with `PermissionError` or `Read-only file system`. This check cannot show
   that the audit trail, feedback or plan writes succeed: those writers are container-local, fail
   open, and log a failed write at debug level, which the runtimes (INFO) do not emit. Those
   writes as uid 65532 are verified locally only (the `.audit/trail.jsonl` write above).
4. `diagram-fetch` works on both upstreams: run the two commands in the 3.3.0 check above and
   expect `Saved locally:` with each file `(sha256 verified)`. In private mode those commands work
   only from a network that reaches the VPC endpoints. From anywhere else each prints one line,
   `Not fetched: gateway denied the request (HTTP 403: ...)` or `Not fetched: runtime denied the
   request (HTTP 403: ...)`, plus a hint, exits 3 and writes nothing (exit 2 is a local config
   error). When no workstation network path exists, run the end-to-end diagram test through the
   private access probe instead (`enable_private_access_probe = true`, then disable it):

   ```bash
   # The password-flow Cognito env (GAC_COGNITO_*; --via-probe always uses the password flow,
   # GAC_AUTH_FLOW does not apply); the password comes from GAC_COGNITO_PASSWORD or a no-echo
   # prompt. AWS credentials that may invoke the probe.
   export GAC_PROBE_REGION=<region>
   ./run.sh diagram-fetch --via-probe "$(terraform -chdir=agentcore/terraform output -raw private_access_probe_function_name)" \
     "draw our private network path"
   # expect "Saved locally:" with outputs/diagrams/private-request-path/ .drawio, .svg, .png and
   # .report.json, each "(sha256 verified)", then "Quality score: <n> (threshold <t>, pass)"
   ```

   Exit codes for `--via-probe`: 0 saved; 1 the local writer refused the files (for example a
   failed sha256 check, a traversal-shaped name or an existing file without `--overwrite`); 2 a
   local config error, or `--upstream` / a non-default `--environment` passed with it (the probe
   always uses the gateway and `dev`); 3 an upstream failure. The Lambda client waits 130 s, past
   the probe's 120 s timeout, and does not retry, so a slow cold render prints `Not fetched: probe
   did not answer in time` instead of re-invoking the probe.

   The probe calls `gac___draw_diagram` through the gateway inside the VPC and returns the same
   artefact envelope the bridge consumes; the helper saves it through the bridge's writer, so the
   sha256 check, path-traversal refusal and no-overwrite rule are the bridge's own. This proves the
   gateway, interceptor, MCP runtime and renderer in AWS. It does not prove a workstation's network
   path; that still needs a run from the VPN / Direct Connect network.

Record the result here.

### Testing lessons

- **Token.** Mint the test token with the `frontdoor` app client, which allows
  `ALLOW_ADMIN_USER_PASSWORD_AUTH` and has a client secret, so the request needs a `SECRET_HASH`.
  `frontdoor/cognito_token.py` computes it (`GAC_AUTH_FLOW=password`). The PKCE client cannot
  mint a token this way; it signs in through the browser (`frontdoor/pkce_token.py`, the bridge's
  default).
- **Test 5 needs both calls.** A JWT call to the MCP runtime returns "Authorization method
  mismatch", which proves only that the runtime refuses the JWT method. Use a SigV4 call (5b) for the
  resource-policy proof.
- **Deny first, then allow.** Run test 12, then 2 and 4a from outside, before any in-VPC test.
- **Disable the probe after testing.** Set `enable_private_access_probe = false` and apply.
- **Diagram end to end without a network path.** In private mode, from outside the VPC, the gateway
  answers HTTP 403 (JSON-RPC -32002 "Authorization error - Insufficient permissions") and the
  orchestrator runtime answers 403 "explicit deny in a resource-based policy". That is the private
  mode working, not a fault: `diagram-fetch` reports it in one line and exits 3. Run the diagram
  test through the probe (`--via-probe`, check 4 above) until a workstation path exists.
- **IAM propagation.** On a kit older than 3.2.1 the first private apply can fail creating the
  gateway target; re-run `terraform apply` (Troubleshooting).
- **Teardown is slow.** AgentCore and Lambda ENIs can linger for hours after their owners are
  deleted; re-run `terraform destroy` later rather than deleting ENIs by hand.
- **`terraform test` in a copy.** It loads a local `terraform.tfvars` automatically and then fails a
  mocked run. Run the tests in a copy of the repository without `terraform.tfvars` and `backend.tf`
  (see `agentcore/terraform/README.md`).

### Private access probe (test stand-in)

`enable_private_access_probe = true` (private mode with `deploy_gateway = true`) deploys a small
Python Lambda function in the private subnets. It stands in for a builder workstation connected
through AWS Direct Connect or VPN with private DNS. It resolves the gateway hostname and records
the IPs, POSTs JSON-RPC to the gateway `/mcp` route (`initialize`, `tools/list`, and a `tools/call`
of `gac___companion_kb` with a fixed test question), and, with `call_runtime: true`, calls the
orchestrator runtime through the data-plane endpoint like the stdio bridge. It returns HTTP status
codes, redacted and truncated response bodies, and the resolved IPs.

- **Diagram mode.** The payload `{token, diagram: "<request text>"}` (1 to 500 characters, no
  control characters) runs `initialize`, `notifications/initialized` and a `tools/call` of
  `gac___draw_diagram` (fixed in `GAC_PROBE_DIAGRAM_TOOL_NAME`; the caller cannot choose a tool)
  with `include_artifacts: true`, and returns the tool's full result under `result`: the gated
  text and the `gac.diagram/1` envelope (drawio, svg, png base64, sha256 and sizes). The tool
  result is read up to the design's 5 MiB upstream cap and the whole result must stay under
  Lambda's 6 MB synchronous response limit; over either, it returns a clear error and no
  artefacts. The token is redacted from the text; an artefact that contains a token-shaped string
  is withheld. Logs hold `step=` lines only, never artefact content. Same role, network, timeout
  (120 s; the diagram call has 90 s) and IAM as the other modes. Use it with
  `./run.sh diagram-fetch --via-probe <function>` (post-deploy check 4 for 3.3.1).

- **Token.** You pass your own front-door token in the invocation payload. The probe never stores,
  logs or returns it. It follows no redirect, ignores proxy settings, verifies TLS, and sends the
  token only to the gateway host and the AgentCore data-plane host. Anyone with
  `lambda:InvokeFunction` on the probe can invoke it, but gets nothing back without their own
  valid token.
- **Invoke.** Prerequisites: AWS CLI v2, `jq`, and `GAC_GATEWAY_TOKEN` exported in the current
  shell. Run the command from `terraform output -raw private_access_probe_invoke_command`. It reads
  the token with `jq` from the environment, so the token is never in argv or in Terraform output,
  and it writes the result to a mode-600 temporary file that it then removes. Whether AWS CLI v2
  reads `--payload file:///dev/stdin` is UNVERIFIED. If it does not, use the same subshell with
  `tmp="$(mktemp)"; jq -nc '{token: env.GAC_GATEWAY_TOKEN, call_runtime: true}' > "$tmp"`, pass
  `--payload "fileb://$tmp"`, and finish with `rm -f "$tmp" "$out"`. That leaves the token briefly at
  rest in a mode-600 file.
- **Network.** No ingress, no function URL, no resource-based policy, no internet route. HTTPS
  egress goes to the client endpoint security group only (the module's client SG, or
  `existing_client_endpoint_security_group_ids` on a shared network). Lambda delivers function logs
  and traces outside the execution environment, so the probe has no path to the Logs or X-Ray
  endpoints.
- **No log lines?** Check in this order: (1) `aws lambda get-function-configuration` shows
  `LoggingConfig` with `ApplicationLogLevel` `INFO` and the probe log group; (2) temporarily remove
  the `ArnEquals lambda:SourceFunctionArn` condition from `ProbeLogs` in `probe.tf` (the boundary
  shares the statement list), re-apply, re-invoke, and record the result; (3) only then add one
  egress and one ingress rule to the runtime-only endpoint SG.
- **How a real workstation differs.** It reaches the VPC over Direct Connect or Site-to-Site /
  Client VPN instead of sitting in the private subnets. It resolves the endpoints' private DNS
  through a Route 53 Resolver inbound endpoint (`enable_resolver_inbound_endpoint`, or your own),
  with an on-premises conditional forwarder for `bedrock-agentcore.<region>.amazonaws.com` (this
  also covers `<id>.gateway.bedrock-agentcore.<region>.amazonaws.com`) pointing at the
  `resolver_inbound_ip_addresses` output. Its network range in `private_client_cidrs` admits it on
  the client endpoint SG, not a security-group reference. It mints its own Cognito token.
- **Disable after testing.** Set `enable_private_access_probe = false` and apply. Lambda's VPC
  (Hyperplane) ENIs can take a long time to release, so deleting the probe security group may wait
  or fail with `DependencyViolation`. That is expected: re-run `terraform apply` later, and do not
  delete the ENIs by hand.

### Route 53 Resolver inbound endpoint (optional)

`enable_resolver_inbound_endpoint = true` (private mode) creates an inbound endpoint with one IP in
each private subnet and a security group that admits DNS (TCP and UDP 53) only from
`resolver_inbound_client_cidrs` (default: the effective `private_client_cidrs`). Point an
on-premises conditional forwarder for `bedrock-agentcore.<region>.amazonaws.com` at the
`resolver_inbound_ip_addresses` output. The endpoint answers any name the VPC resolver resolves,
for those CIDRs (SD-26). Its security group cannot be changed after creation: a new SG replaces the
endpoint and its IPs, so update the forwarders. Tracked connections can cap it at about 1500
queries per second per IP; add IPs rather than widening rules
([AWS guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries-values.html)).
Cost: [`COST.md`](COST.md).

**Pin-bump checklist (ADOT).** When you bump `aws-opentelemetry-distro` in either image, check that
the MCP instrumentor is still named `aws_mcp` (the `opentelemetry_instrumentor` entry point) and
that no other instrumentor now captures tool content, then re-run test 8. Offline check for 0.22.0
(done for kit 3.2.2): the wheel's `entry_points.txt` still maps `aws_mcp` under
`[opentelemetry_instrumentor]` to `amazon.opentelemetry.distro.instrumentation.mcp:McpInstrumentor`,
so `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=aws_mcp` (`locals.tf`) still names it. The other
instrumentors it lists are `aws_crewai`, `aws_langchain`, `aws_llama-index` and
`aws_openai_agents`, none of which the runtimes import. Test 8 after the rebuild is still pending.
To repeat the check:
`pip download --no-deps --only-binary=:all: aws-opentelemetry-distro==<version> -d /tmp/adot`, then
`unzip -p /tmp/adot/*.whl '*entry_points.txt'`.

## Teardown

Before you destroy: set `enable_private_access_probe = false` and apply (its ENIs release slowly),
and remove the `generated/` front-door files you distributed to builders (they name endpoints that
are about to disappear).

```bash
cd agentcore/terraform
terraform destroy      # a human runs this
```

Buckets/repos use `force_destroy` (dev-friendly); for production set them to retain (SD-1 in
[`SECURITY.md`](SECURITY.md)). KMS keys enter a
pending-deletion window rather than deleting immediately.

In private mode, AgentCore runtime ENIs can persist for up to 8 hours after the runtimes are
deleted ([agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)),
so deleting the runtime SG or the sandbox subnets can fail with `DependencyViolation`. Re-run
`terraform destroy` later; the resource policies are destroyed before their runtimes and gateway.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pii_backend="guardrails"` fails at plan | Set `create_bedrock_guardrail=true` or supply `guardrail_id` |
| Runtime `bedrock:InvokeModel` denied | The inference-profile id is not valid in `aws_region`, or model access is off |
| Gateway target `FAILED` | MCP runtime crashed on boot — check its CloudWatch logs |
| `image tag ... immutable` on redeploy | Bump `image_tag` (immutable tags are intentional) |
| Plan fails with a `network_guards` precondition | Read the message: missing VPC inputs, empty `private_client_cidrs` or `knowledge_admin_principal_arns`, VPC DNS off, unsupported AZs, a subnet route table not covered by the S3 endpoint, or public mode without `acknowledge_public_endpoint` |
| Plan error "no matching VPC Endpoint Service" | The service is not offered in this Region |
| Endpoint create fails: private DNS name already owned | Another endpoint in the VPC owns that service's private DNS; set `existing_interface_endpoint_ids` (and `existing_endpoint_security_group_ids`) to reuse it |
| S3 gateway endpoint create fails | The route table already has an S3 gateway endpoint; set `existing_s3_gateway_endpoint_id` |
| Runtime stuck, not READY; logs show ECR/S3 timeouts | A missing endpoint, SG egress or route; check the S3 endpoint is on every subnet's route table |
| AccessDenied on `aws_s3_bucket.kb` or `aws_s3_object.overlay_seed` during plan | The runner is not in `knowledge_admin_principal_arns`. An account admin adds it and runs `terraform apply -refresh=false -target=aws_s3_bucket_policy.kb` (`PutBucketPolicy` is never denied), then a normal plan; or edits the policy with `aws s3api put-bucket-policy` or the console. Only if the policy itself was corrupted to deny management does it need the account root user ([S3 VPC-endpoint policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-bucket-policies-vpc-endpoint.html)) |
| `aws s3 cp` by a curator fails with KMS AccessDenied | The principal is not in `knowledge_admin_principal_arns`, its identity policy lacks the KMS actions on `kb_kms_key_arn`, or the deployment is in public mode (SD-22) |
| `build_and_push.sh` fails with 403 on `BatchGetImage` | The CI/runner role is not in `knowledge_admin_principal_arns` (a push also needs `BatchGetImage`) |
| Runtime cannot pull its image (ECR AccessDenied in the runtime status) | Follow the test 13b fallback order (`aws:ViaAWSService` first, then `ecr_pull_restriction = false`) |
| Gateway target `FAILED` with AssumeRole denied (private mode) | Follow the test 5c fallback (gateway trust `aws:SourceArn`) |
| Gateway target `FAILED` with "Authorization error when sending message" (private mode), no request in the MCP runtime logs | IAM propagation race: the target synced before the gateway role's `InvokeAgentRuntime` grant took effect. Since 3.2.1 the target waits on `terraform_data.gateway_invoke_propagation` (30 s). On an older kit, re-run `terraform apply`: the tainted target is replaced |
| Plan fails on `extra_interface_endpoint_services` | `bedrock-agentcore-control` is rejected by design (SD-21) |
| Bridge reports "endpoint not reachable from this network" or HTTP 403 | You are outside the approved networks; see [`FRONT-DOORS.md`](FRONT-DOORS.md) |
| `destroy` fails with `DependencyViolation` | Lingering AgentCore ENIs (up to 8 hours); re-run later |
| Disabling the probe waits or fails on its security group | Lambda Hyperplane ENIs not yet released; re-run `terraform apply` later |
| Plan fails: `auth_mode="none" is refused on a reused or shared bedrock-agentcore endpoint` | Use `auth_mode = cognito` or `entraid`, or a dedicated network (SD-27) |
| Plan fails: the probe needs `existing_client_endpoint_security_group_ids` | Set it from the platform output of the same name |
| Apply fails creating `aws_cognito_resource_server.engine` (identifier exists) | Another companion in the shared pool uses the same identifier; set a unique `engine_resource_server_identifier` |
| Platform adoption plan shows resource changes beyond the three 3.2.2 additions and the two state access-log updates | The tfvars differ from the original platform; stop and fix them |

## Related resources

- [`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) — full inputs & resources.
- [`FRONT-DOORS.md`](FRONT-DOORS.md) — connect Kiro / Claude Code after deploy.
- [`SECURITY.md`](SECURITY.md) — security model + accepted security debt.
- [`COST.md`](COST.md) — what the deployed footprint costs.
