# `modules/private-network`: private network for AgentCore companions

Builds the network that keeps a companion's knowledge plane off the internet:

- `create_vpc = true`: a VPC with two private subnets in AgentCore-supported AZs, one route table
  (local route plus the S3 gateway endpoint), and the default security group stripped. No internet
  gateway, no NAT gateway, no Elastic IP. VPC Flow Logs (`flow_logs.tf`): ALL traffic to the
  `/vpc/<name_prefix>-private/flow-logs` log group, 365-day retention, encrypted with its own
  rotating CMK, delivered by a role trusted only by `vpc-flow-logs.amazonaws.com` in this account.
- interface endpoints with private DNS for `interface_endpoint_services`;
- an optional S3 gateway endpoint (`create_s3_gateway_endpoint`) with the caller's policy;
- two endpoint security groups: `client` (the endpoints in `client_endpoint_services`, HTTPS from
  `client_cidrs`) and `runtime` (every other endpoint). Neither can carry a `/0` CIDR.

- optionally (`enable_resolver_inbound_endpoint`, off by default) a Route 53 Resolver inbound
  endpoint with one IP per subnet and a security group that admits DNS (TCP/UDP 53) from
  `resolver_client_cidrs` (default `client_cidrs`) only. On-premises DNS servers on Direct Connect
  or VPN forward `bedrock-agentcore.<region>.amazonaws.com` to those IPs. Do53 only. The endpoint's
  security group cannot be changed after creation (a new SG replaces the endpoint and its IPs),
  and tracked connections can cap it at about 1500 queries per second per IP
  ([AWS guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries-values.html)).

It never creates a `bedrock-agentcore-control` endpoint (validation rejects it, SD-21).

## Where it is used

| Caller | Address | Shape |
|---|---|---|
| Companion stack (`../../network.tf`) | `module.private_network[0]` | `create_vpc = true` (sandbox) or an existing VPC; endpoint policies name this companion's resources |
| Platform stack (`../../platform/network.tf`) | `module.network[0]` | One shared VPC that several companions consume through `vpc_id` and the `existing_*` inputs |

Per-companion controls stay in the companion stack: resource policies, the runtime security group
and its rules (including ingress from the runtime SG on these endpoint SGs), the gateway, the KB
bucket and key policies, the ECR repository policies, and IAM.

## Ordering

`guards_ready` takes the caller's guard resource id. The endpoints reference it in a
precondition, so they wait for the guards without a module-level `depends_on` (which would defer
every data source in the module to apply time).

## Outputs

`vpc_id`, `vpc_cidr`, `private_subnet_ids`, `private_route_table_ids`, `az_ids`,
`supported_az_ids`, `interface_endpoint_ids`, `interface_endpoint_dns_names`,
`s3_gateway_endpoint_id`, `s3_prefix_list_id`, `endpoint_security_group_ids` (map `client` /
`runtime`) and `endpoint_security_group_id_list`. They map 1:1 onto the companion consumer inputs.

## Upgrading a companion created before 3.2.0

`../../moved.tf` moves the resources that used to live in `network.tf` to
`module.private_network[0]`. Run `terraform init` once (local module install); the next plan shows
`has moved to` lines and no replacement.
