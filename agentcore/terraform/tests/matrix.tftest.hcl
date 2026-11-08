# Plan-only combination matrix for the companion stack (Terraform >= 1.7). Every provider is
# mocked: no AWS calls, no credentials, no Docker. Run from agentcore/terraform:
#   terraform init -backend=false && terraform test
#
# Assertion rule: under a mocked provider the `json` of an aws_iam_policy_document is a random
# string, so no assertion reads `.json` or a policy argument built from it. Assertions read the
# documents' configured `statement` blocks instead.
#
# Each expect_failures case names its passing twin.

mock_provider "aws" {
  # Computed values are known at plan so assertions can read them.
  override_during = plan

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:sts::123456789012:assumed-role/gac-deployer/session"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "ap-southeast-2"
      name   = "ap-southeast-2"
    }
  }
  mock_data "aws_iam_session_context" {
    defaults = {
      issuer_arn = "arn:aws:iam::123456789012:role/gac-deployer"
    }
  }
  mock_data "aws_availability_zones" {
    defaults = {
      zone_ids = ["apse2-az1", "apse2-az2", "apse2-az3"]
    }
  }
  mock_data "aws_vpc" {
    defaults = {
      enable_dns_support   = true
      enable_dns_hostnames = true
      cidr_block           = "10.0.0.0/16"
    }
  }
  mock_data "aws_route_tables" {
    defaults = {
      ids = ["rtb-0123456789abcdef0"]
    }
  }
  mock_data "aws_vpc_endpoint" {
    defaults = {
      vpc_id            = "vpc-0123456789abcdef0"
      vpc_endpoint_type = "Gateway"
      service_name      = "com.amazonaws.ap-southeast-2.s3"
      route_table_ids   = ["rtb-0123456789abcdef0"]
    }
  }
  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id = "pl-0123456789abcdef0"
    }
  }
  # A syntactically valid policy JSON so IAM arguments validate at plan (never asserted on).
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id = "ap-southeast-2_Example01"
    }
  }
}

mock_provider "archive" {
  override_during = plan
}
mock_provider "local" {
  override_during = plan
}
mock_provider "null" {
  override_during = plan
}
mock_provider "random" {
  override_during = plan
}

# Distinct AZ IDs per existing subnet (one un-keyed override would give both the same AZ).
override_data {
  target          = data.aws_subnet.existing["subnet-0123456789abcdef0"]
  override_during = plan
  values = {
    vpc_id               = "vpc-0123456789abcdef0"
    availability_zone_id = "apse2-az1"
  }
}

override_data {
  target          = data.aws_subnet.existing["subnet-0123456789abcdef1"]
  override_during = plan
  values = {
    vpc_id               = "vpc-0123456789abcdef0"
    availability_zone_id = "apse2-az2"
  }
}

# Consume path: the shared pool's groups include this companion's entitlement group, so every
# consume-path run passes the existence check (E8 overrides it with a missing group).
override_data {
  target          = data.aws_cognito_user_groups.entitlement[0]
  override_during = plan
  values = {
    groups = [
      { group_name = "gactest-users", description = "", precedence = 0, role_arn = "" },
      { group_name = "other-users", description = "", precedence = 0, role_arn = "" },
    ]
  }
}

# Distinct, known client ids so the allow-list assertions can tell the clients apart.
override_resource {
  target          = aws_cognito_user_pool_client.frontdoor[0]
  override_during = plan
  values = {
    id = "examplefrontdoorclient01"
  }
}

override_resource {
  target          = aws_cognito_user_pool_client.oauth_frontdoor[0]
  override_during = plan
  values = {
    id = "exampleoauthclient000001"
  }
}

override_resource {
  target          = aws_cognito_user_pool_client.m2m[0]
  override_during = plan
  values = {
    id = "examplem2mclient0000001"
  }
}

# Shared inputs: private create_vpc, owned Cognito pool, gateway on, no Docker, no files.
variables {
  aws_region                     = "ap-southeast-2"
  name_prefix                    = "gactest"
  auto_build_push_images         = false
  generate_frontdoor_config      = false
  network_access                 = "private"
  create_vpc                     = true
  deploy_gateway                 = true
  enable_memory                  = true
  knowledge_admin_principal_arns = ["arn:aws:iam::123456789012:role/gac-deployer"]
}

# M1: private, create_vpc, owned pool, gateway, memory.
run "m1_private_create_vpc_owned_pool" {
  command = plan

  assert {
    condition     = length(module.private_network) == 1
    error_message = "the private network module must be called in private mode"
  }
  assert {
    condition     = length(module.private_network[0].private_subnet_ids) == 2
    error_message = "create_vpc must create two private subnets"
  }
  assert {
    condition     = length(aws_cognito_user_pool.this) == 1
    error_message = "the owned-pool path must create the pool"
  }
  assert {
    condition     = contains(keys(module.private_network[0].interface_endpoint_ids), "bedrock-agentcore.gateway") && !contains(keys(module.private_network[0].interface_endpoint_ids), "bedrock-agentcore-control")
    error_message = "gateway endpoint created; control-plane endpoint never created"
  }
  assert {
    condition     = toset(keys(local.vpce_sg_ids)) == toset(["client", "runtime"])
    error_message = "both endpoint security groups are created"
  }
  assert {
    condition     = !contains([for s in data.aws_iam_policy_document.memory_resource[0].statement : s.sid], "DenyOtherPrincipalsOnSharedEndpoint")
    error_message = "shared-endpoint Memory deny must not render for create_vpc"
  }
  assert {
    condition     = length(module.private_network[0].resolver_client_cidrs) == 0
    error_message = "the Resolver inbound endpoint is off by default"
  }
  assert {
    condition     = length(aws_lambda_function.private_access_probe) == 0 && output.private_access_probe_invoke_command == ""
    error_message = "the private access probe is off by default"
  }
  assert {
    condition     = length(terraform_data.gateway_invoke_propagation) == 1
    error_message = "private mode waits for gateway invoke grants to propagate before the target"
  }
}

# M2: M1 plus the private access probe.
run "m2_probe_on_create_vpc" {
  command = plan

  variables {
    enable_private_access_probe = true
  }

  assert {
    condition     = length(aws_lambda_function.private_access_probe) == 1 && length(aws_lambda_function.private_access_probe[0].vpc_config) == 1
    error_message = "the probe is a VPC Lambda function"
  }
  assert {
    condition     = length([for s in data.aws_iam_policy_document.probe[0].statement : s if anytrue([for a in s.actions : startswith(a, "bedrock-agentcore:")])]) == 0
    error_message = "probe role must hold no bedrock-agentcore action"
  }
  assert {
    condition     = toset([for s in data.aws_iam_policy_document.probe[0].statement : s.sid]) == toset(["ProbeLogs", "VpcEni", "XRayTracing", "ProbeDeadLetterQueue"])
    error_message = "probe policy statements"
  }
  assert {
    condition     = length(aws_vpc_security_group_egress_rule.probe_to_existing_endpoints) == 0 && length(aws_vpc_security_group_egress_rule.probe_to_client_endpoints) == 1
    error_message = "on create_vpc the probe reaches the module client SG only"
  }
  assert {
    condition     = aws_lambda_function.private_access_probe[0].timeout == 120 && aws_lambda_function.private_access_probe[0].logging_config[0].application_log_level == "INFO"
    error_message = "probe timeout and log level"
  }
  assert {
    condition     = strcontains(output.private_access_probe_invoke_command, "env.GAC_GATEWAY_TOKEN") && !strcontains(output.private_access_probe_invoke_command, "$${") && strcontains(output.private_access_probe_invoke_command, "--function-name gactest-private-access-probe")
    error_message = "invoke command reads the token from the environment and is fully rendered"
  }
}

# M2b: shared-network consumer (M3 inputs) with the probe and the client SG list: rules only on
# the client SG (passing twin of M2c and M2d).
run "m2b_probe_on_shared_network" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids        = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress            = true
    private_client_cidrs                        = ["10.0.0.0/16"]
    enable_private_access_probe                 = true
    existing_client_endpoint_security_group_ids = ["sg-0123456789abcdef0"]
  }

  assert {
    condition     = length(aws_vpc_security_group_egress_rule.probe_to_existing_endpoints) == 1 && length(aws_vpc_security_group_ingress_rule.existing_endpoint_from_probe) == 1
    error_message = "one probe egress and one ingress rule, on the client SG only"
  }
  assert {
    condition     = aws_vpc_security_group_egress_rule.probe_to_existing_endpoints["sg-0123456789abcdef0"].referenced_security_group_id == "sg-0123456789abcdef0"
    error_message = "the probe rule targets the client SG"
  }
  assert {
    condition     = length(aws_vpc_security_group_egress_rule.probe_to_client_endpoints) == 0
    error_message = "no module client SG on a fully reused network"
  }
}

# M2c: the same without existing_client_endpoint_security_group_ids is refused (G-probe-client-sg;
# passing twin: M2b).
run "m2c_probe_shared_network_without_client_sgs_refused" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
    enable_private_access_probe          = true
  }

  expect_failures = [terraform_data.network_guards]
}

# M2d: a client SG that is not in existing_endpoint_security_group_ids is refused
# (G-client-sg-subset; passing twin: M2b).
run "m2d_client_sg_not_subset_refused" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids        = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress            = true
    private_client_cidrs                        = ["10.0.0.0/16"]
    enable_private_access_probe                 = true
    existing_client_endpoint_security_group_ids = ["sg-0123456789abcdef9"]
  }

  expect_failures = [terraform_data.network_guards]
}

# M7: probe in public mode (with acknowledgement) is refused (G-probe-private; passing twin: M2).
run "m7_probe_public_refused" {
  command = plan

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
    enable_private_access_probe = true
  }

  expect_failures = [terraform_data.network_guards]
}

# M8: probe without a gateway is refused (G-probe-gateway; passing twin: M2).
run "m8_probe_without_gateway_refused" {
  command = plan

  variables {
    deploy_gateway              = false
    enable_private_access_probe = true
  }

  expect_failures = [terraform_data.network_guards]
}

# M3: private, consuming an existing network (dummy ids), every endpoint reused.
run "m3_private_existing_network" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
  }

  assert {
    condition     = length(module.private_network[0].interface_endpoint_ids) == 0
    error_message = "every endpoint is reused: the module creates none"
  }
  assert {
    condition     = module.private_network[0].s3_gateway_endpoint_id == ""
    error_message = "the reused S3 gateway endpoint is not recreated"
  }
  assert {
    condition     = length(local.vpce_sg_ids) == 0
    error_message = "no module endpoint security groups when every endpoint is reused"
  }
  assert {
    condition     = length(aws_vpc_security_group_egress_rule.runtime_to_existing_endpoints) == 2
    error_message = "runtime egress to each reused endpoint SG"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.existing_endpoint_from_runtime) == 2
    error_message = "manage_existing_endpoint_ingress admits the runtime SG on each reused endpoint SG"
  }
  assert {
    condition     = contains([for s in data.aws_iam_policy_document.memory_resource[0].statement : s.sid], "DenyOtherPrincipalsOnSharedEndpoint")
    error_message = "shared-endpoint Memory deny missing"
  }
}

# M3b: M3 plus a consumer-side extra data-plane action: the plan succeeds with the
# shared_endpoint_extras warning (passing twin without the warning: M3).
run "m3b_shared_endpoint_extras_warning" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
    dataplane_endpoint_extra_actions     = ["bedrock-agentcore:ExampleAction"]
  }

  expect_failures = [check.shared_endpoint_extras]
}

# M3c: M1 plus additional_dataplane_vpce_ids (hub VPC path): the Memory shared-endpoint deny
# renders (passing twin without the statement: M1).
run "m3c_hub_dataplane_endpoint_memory_deny" {
  command = plan

  variables {
    additional_dataplane_vpce_ids = ["vpce-0123456789abcdef0"]
  }

  assert {
    condition     = contains([for s in data.aws_iam_policy_document.memory_resource[0].statement : s.sid], "DenyOtherPrincipalsOnSharedEndpoint")
    error_message = "shared-endpoint Memory deny missing on the hub-VPC path"
  }
}

# M3d: M3 plus a custom kb_bucket_name: warning to list it in the platform patterns
# (passing twin: M3).
run "m3d_custom_kb_bucket_on_shared_network_warning" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
    kb_bucket_name                       = "team-knowledge-store"
  }

  expect_failures = [check.shared_endpoint_extras]
}

# M5: public with acknowledgement (passing twin of M6).
run "m5_public_acknowledged" {
  command = plan

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
  }

  assert {
    condition     = length(module.private_network) == 0
    error_message = "public mode calls no private network module"
  }
  assert {
    condition     = length(aws_security_group.runtime) == 0
    error_message = "public mode creates no runtime security group"
  }
  assert {
    condition     = length(terraform_data.gateway_invoke_propagation) == 0
    error_message = "public mode (OAuth M2M target) needs no IAM propagation wait"
  }
}

# M6: public without acknowledgement is refused (passing twin: M5).
run "m6_public_unacknowledged_refused" {
  command = plan

  variables {
    network_access = "public"
    create_vpc     = false
  }

  expect_failures = [terraform_data.network_guards]
}

# M9: Resolver inbound endpoint on, private create_vpc: DNS rules use the VPC CIDR default
# (passing twin of M9b).
run "m9_resolver_inbound_create_vpc" {
  command = plan

  variables {
    enable_resolver_inbound_endpoint = true
  }

  assert {
    condition     = toset(module.private_network[0].resolver_client_cidrs) == toset(["10.80.0.0/16"])
    error_message = "resolver DNS rules must use the effective client CIDRs (the sandbox VPC CIDR)"
  }
}

# M9b: Resolver inbound endpoint in public mode is refused (G-resolver; passing twin: M9).
run "m9b_resolver_public_refused" {
  command = plan

  variables {
    network_access                   = "public"
    acknowledge_public_endpoint      = true
    create_vpc                       = false
    enable_resolver_inbound_endpoint = true
  }

  expect_failures = [terraform_data.network_guards]
}

# M10: auth_mode none on a reused data-plane endpoint is refused (G-none-shared; passing twin:
# M10b). deploy_gateway=false so only G-none-shared can fail.
run "m10_auth_none_on_shared_endpoint_refused" {
  command = plan

  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
    auth_mode                            = "none"
    allow_unauthenticated                = true
    deploy_gateway                       = false
  }

  expect_failures = [terraform_data.network_guards]
}

# M10b: auth_mode none on a dedicated create_vpc network plans (twin of M10).
run "m10b_auth_none_dedicated_network_plans" {
  command = plan

  variables {
    auth_mode             = "none"
    allow_unauthenticated = true
    deploy_gateway        = false
  }

  assert {
    condition     = length(module.private_network) == 1
    error_message = "the dedicated network plans with auth_mode none"
  }
}

# M4: consumed pool (shared identity) with a per-companion resource-server identifier. Network
# shape: M1 (private create_vpc). Passing twin of the owned-pool path M1.
run "m4_consumed_pool_custom_resource_server" {
  command = plan

  variables {
    manage_identity_pool              = false
    existing_user_pool_id             = "ap-southeast-2_Example02"
    existing_oidc_issuer              = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example02"
    engine_resource_server_identifier = "team2-engine"
  }

  assert {
    condition     = length(aws_cognito_user_pool.this) == 0 && length(aws_cognito_resource_server.engine) == 1
    error_message = "consume path creates no pool but its own resource server"
  }
  assert {
    condition     = aws_cognito_resource_server.engine[0].identifier == "team2-engine" && aws_cognito_resource_server.engine[0].user_pool_id == "ap-southeast-2_Example02"
    error_message = "resource server identifier and pool"
  }
  assert {
    condition     = output.cognito_oauth_scope == "team2-engine/invoke"
    error_message = "scope follows the identifier"
  }
}

# M4b: owned pool keeps the default identifier (no change for existing deployments).
run "m4b_owned_pool_default_resource_server" {
  command = plan

  assert {
    condition     = aws_cognito_resource_server.engine[0].identifier == "gac-engine" && output.cognito_oauth_scope == "gac-engine/invoke"
    error_message = "default identifier unchanged"
  }
}

# M11: diagram_enabled = false adds exactly GAC_DIAGRAM_ENABLED=false to the runtime env; the
# default adds nothing (passing twin of M11b).
run "m11_diagram_disabled_env" {
  command = plan
  variables {
    diagram_enabled = false
  }
  assert {
    condition     = local.runtime_env["GAC_DIAGRAM_ENABLED"] == "false"
    error_message = "diagram_enabled = false must set GAC_DIAGRAM_ENABLED=false"
  }
  assert {
    condition     = length(setsubtract(keys(local.runtime_env), keys(merge(local.base_env, local.data_protection_env, local.memory_env, local.observability_env)))) == 1
    error_message = "diagram_enabled = false must add exactly one env key"
  }
}

run "m11a_diagram_enabled_default_env" {
  command = plan
  assert {
    condition     = !contains(keys(local.runtime_env), "GAC_DIAGRAM_ENABLED")
    error_message = "the default must not add GAC_DIAGRAM_ENABLED"
  }
}

# M11b: a non-hex diagram_icons_sha256 is refused (passing twin: M11a, empty default).
run "m11b_diagram_icons_sha256_invalid_refused" {
  command = plan
  variables {
    diagram_icons_sha256 = "not-a-sha"
  }
  expect_failures = [var.diagram_icons_sha256]
}

# ---- Gateway tool re-sync (D9, AC 15) --------------------------------------------------------

# S1: with the gateway, one sync whose triggers carry the image URI, runtime version and tool
# surface hash (passing twin of S2 and S3).
run "s1_gateway_tool_sync_on" {
  command = plan

  # The mock leaves these two computed values unknown at plan; pin them so the triggers are known.
  override_resource {
    target          = aws_ecr_repository.mcp_runtime
    override_during = plan
    values = {
      repository_url = "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/gactest-mcp-runtime"
    }
  }
  override_resource {
    target          = aws_bedrockagentcore_agent_runtime.mcp_runtime
    override_during = plan
    values = {
      agent_runtime_version = "7"
      agent_runtime_arn     = "arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/gactest_mcp_runtime-Example001"
    }
  }

  assert {
    condition     = length(terraform_data.gateway_tool_sync) == 1
    error_message = "one gateway tool sync with the gateway on"
  }
  assert {
    condition = (
      contains(terraform_data.gateway_tool_sync[0].triggers_replace, local.mcp_runtime_image_uri) &&
      contains(terraform_data.gateway_tool_sync[0].triggers_replace, aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_version) &&
      contains(terraform_data.gateway_tool_sync[0].triggers_replace, local.mcp_tool_surface_hash)
    )
    error_message = "the sync re-runs on a new image, runtime version or tool surface"
  }
  assert {
    condition     = length(terraform_data.gateway_tool_sync[0].triggers_replace) == 3 && can(regex("^[0-9a-f]{40}$", local.mcp_tool_surface_hash))
    error_message = "exactly three triggers; the tool surface hash is a sha1"
  }
}

# S2: no gateway, no sync.
run "s2_gateway_tool_sync_no_gateway" {
  command = plan

  variables {
    deploy_gateway = false
  }

  assert {
    condition     = length(terraform_data.gateway_tool_sync) == 0
    error_message = "no sync without a gateway"
  }
}

# S3: opt-out.
run "s3_gateway_tool_sync_opt_out" {
  command = plan

  variables {
    sync_gateway_tools = false
  }

  assert {
    condition     = length(terraform_data.gateway_tool_sync) == 0
    error_message = "sync_gateway_tools = false creates no sync"
  }
}

# S4: the settle heuristic is bounded 0..120 (passing twin: S1, default 15).
run "s4_gateway_sync_settle_out_of_range_refused" {
  command = plan

  variables {
    gateway_sync_settle_seconds = 121
  }

  expect_failures = [var.gateway_sync_settle_seconds]
}

# ---- Opt-in Bedrock diagram planner (D11, AC 17) ---------------------------------------------

# P1: a valid bedrock planner: the guardrail grant and the four planner env vars (passing twin of
# P2-P5).
run "p1_diagram_planner_bedrock" {
  command = plan

  variables {
    diagram_planner          = "bedrock"
    diagram_planner_model_id = "apac.example.model-v1:0"
    guardrail_id             = "examplegr01"
  }

  assert {
    condition     = contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DiagramPlannerGuardrail")
    error_message = "the bedrock planner grants ApplyGuardrail"
  }
  assert {
    condition     = !contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DiagramPlannerCrossRegionModels")
    error_message = "no cross-Region grant without diagram_planner_model_regions"
  }
  assert {
    condition = (
      local.runtime_env["GAC_DIAGRAM_PLANNER"] == "bedrock" &&
      local.runtime_env["GAC_DIAGRAM_PLANNER_MODEL_ID"] == "apac.example.model-v1:0" &&
      local.runtime_env["GAC_GUARDRAIL_ID"] == "examplegr01" &&
      local.runtime_env["GAC_GUARDRAIL_VERSION"] == "DRAFT"
    )
    error_message = "the four planner env vars"
  }
  assert {
    condition = alltrue([for s in data.aws_iam_policy_document.runtime_exec_inline.statement :
      toset(s.actions) == toset(["bedrock:ApplyGuardrail"]) && toset(s.resources) == toset(["arn:aws:bedrock:ap-southeast-2:123456789012:guardrail/examplegr01"])
    if s.sid == "DiagramPlannerGuardrail"])
    error_message = "the planner guardrail grant is scoped to the effective guardrail"
  }
}

# P2: bedrock without a model id is refused.
run "p2_diagram_planner_no_model_refused" {
  command = plan

  variables {
    diagram_planner          = "bedrock"
    create_bedrock_guardrail = true
  }

  expect_failures = [terraform_data.diagram_planner_guards]
}

# P3: an invalid model id is refused.
run "p3_diagram_planner_invalid_model_refused" {
  command = plan

  variables {
    diagram_planner          = "bedrock"
    diagram_planner_model_id = "Not A Model"
    create_bedrock_guardrail = true
  }

  expect_failures = [var.diagram_planner_model_id]
}

# P4: bedrock without a guardrail is refused.
run "p4_diagram_planner_no_guardrail_refused" {
  command = plan

  variables {
    diagram_planner          = "bedrock"
    diagram_planner_model_id = "apac.example.model-v1:0"
  }

  expect_failures = [terraform_data.diagram_planner_guards]
}

# P5: private mode without the bedrock-runtime endpoint is refused.
run "p5_diagram_planner_private_no_endpoint_refused" {
  command = plan

  variables {
    diagram_planner                 = "bedrock"
    diagram_planner_model_id        = "apac.example.model-v1:0"
    create_bedrock_guardrail        = true
    enable_bedrock_runtime_endpoint = false
  }

  expect_failures = [terraform_data.diagram_planner_guards]
}

# P6: public mode does not need the endpoint (Converse goes to the regional endpoint over TLS).
run "p6_diagram_planner_public_no_endpoint" {
  command = plan

  variables {
    network_access                  = "public"
    acknowledge_public_endpoint     = true
    create_vpc                      = false
    diagram_planner                 = "bedrock"
    diagram_planner_model_id        = "apac.example.model-v1:0"
    guardrail_id                    = "examplegr01"
    enable_bedrock_runtime_endpoint = false
    diagram_planner_model_regions   = ["ap-southeast-2", "ap-northeast-1"]
  }

  assert {
    condition     = contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DiagramPlannerCrossRegionModels")
    error_message = "listed Regions add the cross-Region grant"
  }
  assert {
    condition = alltrue([for s in data.aws_iam_policy_document.runtime_exec_inline.statement :
      toset(s.resources) == toset(["arn:aws:bedrock:ap-southeast-2::foundation-model/*", "arn:aws:bedrock:ap-northeast-1::foundation-model/*"])
      && length(s.condition) == 1
    if s.sid == "DiagramPlannerCrossRegionModels"])
    error_message = "the cross-Region grant covers the listed Regions, through an inference profile only"
  }
}

# P6b: listed Regions add no cross-Region grant while the planner is deterministic (review IAM-1).
run "p6b_model_regions_need_bedrock_planner" {
  command = plan
  variables {
    diagram_planner_model_regions = ["ap-southeast-2", "ap-northeast-1"]
  }
  assert {
    condition     = !contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DiagramPlannerCrossRegionModels")
    error_message = "no cross-Region grant with diagram_planner = deterministic"
  }
}
# P7: the deterministic default adds no planner grant or env; the boundary deny is unconditional
# (both modes, see E2 for public).
run "p7_diagram_planner_default" {
  command = plan

  assert {
    condition     = length(setintersection(toset([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid]), toset(["DiagramPlannerGuardrail", "DiagramPlannerCrossRegionModels"]))) == 0
    error_message = "the default adds no planner grant"
  }
  assert {
    condition     = length(local.diagram_env) == 0 && !contains(keys(local.runtime_env), "GAC_DIAGRAM_PLANNER")
    error_message = "the default adds no planner env"
  }
  assert {
    condition     = contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DenyBoundarySelfModification")
    error_message = "the boundary deny renders in private mode"
  }
}

# ---- Entitlement group (D26, AC 24) and datastore non-exposure (AC 26) ----------------------

# E1: private, gateway, owned pool (the shared inputs): claim rules on the gateway and the
# orchestrator, the group in the owned pool, no existence check, the interceptor re-check env.
run "e1_entitlement_private_gateway_owned_pool" {
  command = plan

  assert {
    condition = alltrue([for c in aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim :
      contains(["cognito:groups"], c.inbound_token_claim_name) && contains(["STRING_ARRAY"], c.inbound_token_claim_value_type) &&
      c.authorizing_claim_match_value[0].claim_match_operator == "CONTAINS" &&
    c.authorizing_claim_match_value[0].claim_match_value[0].match_value_string == "gactest-users"]) && length(aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1
    error_message = "the gateway requires cognito:groups CONTAINS gactest-users"
  }
  assert {
    condition = alltrue([for c in aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim :
      contains(["cognito:groups"], c.inbound_token_claim_name) && contains(["STRING_ARRAY"], c.inbound_token_claim_value_type) &&
      c.authorizing_claim_match_value[0].claim_match_operator == "CONTAINS" &&
    c.authorizing_claim_match_value[0].claim_match_value[0].match_value_string == "gactest-users"]) && length(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1
    error_message = "the orchestrator requires cognito:groups CONTAINS gactest-users"
  }
  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration) == 0
    error_message = "private mode with a gateway: the MCP runtime keeps SigV4 (no JWT authorizer)"
  }
  assert {
    condition     = length(aws_cognito_user_group.entitlement) == 1 && aws_cognito_user_group.entitlement[0].name == "gactest-users" && aws_cognito_user_group.entitlement[0].user_pool_id == aws_cognito_user_pool.this[0].id
    error_message = "the own path creates the group in the owned pool"
  }
  assert {
    condition     = length(data.aws_cognito_user_groups.entitlement) == 0
    error_message = "the own path runs no existence check"
  }
  assert {
    condition     = aws_lambda_function.identity_interceptor[0].environment[0].variables["GAC_REQUIRED_GROUP"] == "gactest-users" && aws_lambda_function.identity_interceptor[0].environment[0].variables["GAC_REQUIRED_GROUP_CLAIM"] == "cognito:groups"
    error_message = "the interceptor re-checks the group"
  }
  assert {
    condition     = length(aws_kms_key.interceptor) == 1 && aws_kms_key.interceptor[0].enable_key_rotation && aws_kms_key.interceptor[0].deletion_window_in_days == 7 && aws_kms_alias.interceptor[0].name == "alias/gactest-interceptor"
    error_message = "the interceptor environment has its own rotating CMK and alias (CKV_AWS_173)"
  }
  assert {
    condition = contains([for s in jsondecode(aws_kms_key.interceptor[0].policy).Statement : s.Sid if s.Sid == "LambdaCryptoForThisFunction" &&
      s.Condition.StringEquals["kms:ViaService"] == "lambda.ap-southeast-2.amazonaws.com" &&
    s.Condition.StringEquals["kms:EncryptionContext:aws:lambda:FunctionArn"] == "arn:aws:lambda:ap-southeast-2:123456789012:function:gactest-gateway-identity-interceptor"], "LambdaCryptoForThisFunction")
    error_message = "the key is usable only through Lambda for this function"
  }
  assert {
    condition     = length(aws_iam_role_policy.interceptor_env_key) == 1
    error_message = "the interceptor role may decrypt with its key"
  }
  assert {
    condition     = length(aws_cognito_user_pool_client.m2m) == 0 && toset(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients) == toset(local.jwt_allowed_clients)
    error_message = "the orchestrator allows the front-door clients only"
  }
}

# E2: public with the gateway: the MCP runtime allows the M2M client ONLY, with no claim; the
# orchestrator excludes the M2M client; the boundary deny is unconditional.
run "e2_entitlement_public_gateway" {
  command = plan

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
  }

  assert {
    condition     = length(aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1 && length(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1
    error_message = "the gateway and the orchestrator carry the claim rule in public mode"
  }
  assert {
    condition     = !contains(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients, aws_cognito_user_pool_client.m2m[0].id)
    error_message = "the orchestrator allow-list excludes the M2M client"
  }
  assert {
    condition     = toset(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients) == toset([aws_cognito_user_pool_client.m2m[0].id])
    error_message = "the MCP runtime allows the gateway's M2M client only"
  }
  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 0
    error_message = "client-credentials tokens carry no groups: no claim rule on the MCP runtime"
  }
  assert {
    condition     = contains([for s in data.aws_iam_policy_document.runtime_exec_inline.statement : s.sid], "DenyBoundarySelfModification")
    error_message = "the boundary deny renders in public mode too"
  }
  assert {
    condition     = length(aws_cognito_user_group.entitlement) == 1 && length(data.aws_cognito_user_groups.entitlement) == 0
    error_message = "own path in public mode: one group, no check"
  }
}

# E3: private without a gateway: the MCP runtime carries the orchestrator's claim and clients.
run "e3_entitlement_private_no_gateway" {
  command = plan

  variables {
    deploy_gateway = false
  }

  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1 && toset(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients) == toset(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients)
    error_message = "without a gateway the MCP runtime has the orchestrator's claim rule and allow-list"
  }
  assert {
    condition = alltrue([for c in aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim :
    contains(["cognito:groups"], c.inbound_token_claim_name) && c.authorizing_claim_match_value[0].claim_match_value[0].match_value_string == "gactest-users"])
    error_message = "the MCP runtime claim rule matches the orchestrator's"
  }
  assert {
    condition     = length(aws_kms_key.interceptor) == 0 && length(aws_kms_alias.interceptor) == 0 && length(aws_iam_role_policy.interceptor_env_key) == 0
    error_message = "no interceptor, no interceptor key"
  }
}

# E4: public without a gateway: the same as E3.
run "e4_entitlement_public_no_gateway" {
  command = plan

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
    deploy_gateway              = false
  }

  assert {
    condition     = length(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1 && length(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1
    error_message = "both runtimes carry the claim rule without a gateway"
  }
  assert {
    condition     = length(aws_cognito_user_pool_client.m2m) == 0 || !contains(aws_bedrockagentcore_agent_runtime.mcp_runtime.authorizer_configuration[0].custom_jwt_authorizer[0].allowed_clients, aws_cognito_user_pool_client.m2m[0].id)
    error_message = "no gateway: the M2M client is not allowed on the MCP runtime"
  }
}

# E5: public with require_entitlement_group = false is refused (passing twin: E2).
run "e5_entitlement_public_opt_out_refused" {
  command = plan

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
    require_entitlement_group   = false
  }

  expect_failures = [terraform_data.auth_guards]
}

# E6: private opt-out (allowed, documented as weaker): no claim, no group, no check, no env.
run "e6_entitlement_private_opt_out" {
  command = plan

  variables {
    require_entitlement_group = false
  }

  assert {
    condition     = length(aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 0 && length(aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 0
    error_message = "the opt-out removes the claim rules"
  }
  assert {
    condition     = length(aws_cognito_user_group.entitlement) == 0 && length(data.aws_cognito_user_groups.entitlement) == 0 && length(aws_lambda_function.identity_interceptor[0].environment) == 0
    error_message = "the opt-out creates no group, runs no check and sets no interceptor env"
  }
}

# E7: consume path, manage_entitlement_group unset: no group, the claim still names
# <name_prefix>-users, one passing existence check (passing twin of E8 and E9).
run "e7_entitlement_consume_path" {
  command = plan

  variables {
    manage_identity_pool  = false
    existing_user_pool_id = "ap-southeast-2_Example02"
    existing_oidc_issuer  = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example02"
  }

  assert {
    condition     = length(aws_cognito_user_group.entitlement) == 0
    error_message = "the consume path creates no group in the shared pool"
  }
  assert {
    condition     = length(data.aws_cognito_user_groups.entitlement) == 1 && data.aws_cognito_user_groups.entitlement[0].user_pool_id == "ap-southeast-2_Example02"
    error_message = "one existence check on the shared pool"
  }
  assert {
    condition = local.entitlement_group == "gactest-users" && alltrue([for c in aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim :
    c.authorizing_claim_match_value[0].claim_match_value[0].match_value_string == local.entitlement_group])
    error_message = "the claim rule names local.entitlement_group"
  }
}

# E8: consume path with the group missing from the shared pool fails the existence check with
# the pinned message (passing twin: E7).
run "e8_entitlement_consume_group_missing_refused" {
  command = plan

  variables {
    manage_identity_pool  = false
    existing_user_pool_id = "ap-southeast-2_Example02"
    existing_oidc_issuer  = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example02"
  }

  override_data {
    target          = data.aws_cognito_user_groups.entitlement[0]
    override_during = plan
    values = {
      groups = [{ group_name = "other-users", description = "", precedence = 0, role_arn = "" }]
    }
  }

  expect_failures = [data.aws_cognito_user_groups.entitlement]
}

# E9: consume path with the existence check off: zero data-source instances.
run "e9_entitlement_consume_check_off" {
  command = plan

  variables {
    manage_identity_pool           = false
    existing_user_pool_id          = "ap-southeast-2_Example02"
    existing_oidc_issuer           = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example02"
    check_entitlement_group_exists = false
  }

  assert {
    condition     = length(data.aws_cognito_user_groups.entitlement) == 0 && length(aws_cognito_user_group.entitlement) == 0
    error_message = "no check and no group"
  }
}

# E10: consume path with manage_entitlement_group = true is refused (passing twin: E7).
run "e10_entitlement_consume_manage_refused" {
  command = plan

  variables {
    manage_identity_pool     = false
    existing_user_pool_id    = "ap-southeast-2_Example02"
    existing_oidc_issuer     = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example02"
    manage_entitlement_group = true
  }

  expect_failures = [terraform_data.auth_guards]
}

# E11: own path with manage_entitlement_group = false: zero groups and zero checks (N16: the
# owned pool does not exist at first plan; the deployer creates the group elsewhere).
run "e11_entitlement_own_path_unmanaged" {
  command = plan

  variables {
    manage_entitlement_group = false
  }

  assert {
    condition     = length(aws_cognito_user_group.entitlement) == 0 && length(data.aws_cognito_user_groups.entitlement) == 0
    error_message = "own path, group managed elsewhere: no group and no check"
  }
  assert {
    condition     = length(aws_bedrockagentcore_gateway.this[0].authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim) == 1
    error_message = "the claim rule is still enforced"
  }
}

# E12: a custom group and claim name reach the authorizers and the interceptor.
run "e12_entitlement_custom_group_and_claim" {
  command = plan

  variables {
    entitlement_group_name = "team-a-users"
    entitlement_claim_name = "groups"
  }

  assert {
    condition = alltrue([for c in aws_bedrockagentcore_agent_runtime.orchestrator.authorizer_configuration[0].custom_jwt_authorizer[0].custom_claim :
    contains(["groups"], c.inbound_token_claim_name) && c.authorizing_claim_match_value[0].claim_match_value[0].match_value_string == "team-a-users"])
    error_message = "the custom claim and group reach the orchestrator rule"
  }
  assert {
    condition     = aws_lambda_function.identity_interceptor[0].environment[0].variables["GAC_REQUIRED_GROUP"] == "team-a-users" && aws_lambda_function.identity_interceptor[0].environment[0].variables["GAC_REQUIRED_GROUP_CLAIM"] == "groups"
    error_message = "the interceptor gets the custom group and claim"
  }
}

# D1: datastore non-exposure, private mode (AC 26).
run "d1_datastores_not_exposed_private" {
  command = plan

  # M3 network shape: the reused endpoint ids are known at plan, so the policies render.
  variables {
    create_vpc                      = false
    vpc_id                          = "vpc-0123456789abcdef0"
    private_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
    private_route_table_ids         = ["rtb-0123456789abcdef0"]
    existing_s3_gateway_endpoint_id = "vpce-0123456789abcdef9"
    existing_interface_endpoint_ids = {
      "bedrock-agentcore"         = "vpce-0123456789abcdef1"
      "bedrock-agentcore.gateway" = "vpce-0123456789abcdef2"
      "bedrock-runtime"           = "vpce-0123456789abcdef3"
      "ecr.api"                   = "vpce-0123456789abcdef4"
      "ecr.dkr"                   = "vpce-0123456789abcdef5"
      "logs"                      = "vpce-0123456789abcdef6"
      "xray"                      = "vpce-0123456789abcdef7"
      "comprehend"                = "vpce-0123456789abcdef8"
    }
    existing_endpoint_security_group_ids = ["sg-0123456789abcdef0", "sg-0123456789abcdef1"]
    manage_existing_endpoint_ingress     = true
    private_client_cidrs                 = ["10.0.0.0/16"]
  }

  # The mock leaves these ARNs unknown at plan; pin them so the rendered policies are known.
  override_resource {
    target          = aws_s3_bucket.kb
    override_during = plan
    values = {
      arn = "arn:aws:s3:::gactest-kb-example"
    }
  }
  override_resource {
    target          = aws_s3_bucket.kb_logs
    override_during = plan
    values = {
      arn = "arn:aws:s3:::gactest-kb-logs-example"
    }
  }
  override_resource {
    target          = aws_iam_role.runtime_exec
    override_during = plan
    values = {
      arn = "arn:aws:iam::123456789012:role/gactest-runtime-exec-role"
    }
  }

  assert {
    condition = alltrue([for b in [aws_s3_bucket_public_access_block.kb, aws_s3_bucket_public_access_block.kb_logs] :
    b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets])
    error_message = "both KB buckets block public access (all four flags)"
  }
  assert {
    condition = alltrue([for s in concat(jsondecode(aws_s3_bucket_policy.kb.policy).Statement, jsondecode(aws_s3_bucket_policy.kb_logs.policy).Statement, jsondecode(local.ecr_repo_policy).Statement) :
    try(s.Condition, null) != null if contains([jsonencode("*"), jsonencode({ AWS = "*" })], jsonencode(s.Principal))])
    error_message = "no bucket or ECR policy grants an unconditioned Principal *"
  }
  assert {
    condition = alltrue([for s in data.aws_iam_policy_document.memory_resource[0].statement :
    length(s.condition) > 0 if anytrue([for p in s.principals : contains(p.identifiers, "*")])])
    error_message = "the Memory policy has no unconditioned Principal *"
  }
  assert {
    condition     = contains([for s in jsondecode(aws_s3_bucket_policy.kb.policy).Statement : s.Sid if s.Effect == "Deny" && s.Condition == { Bool = { "aws:SecureTransport" = "false" } }], "DenyInsecureTransport")
    error_message = "the KB bucket policy keeps its TLS-only deny"
  }
  assert {
    condition = (!contains(local.client_cidrs_effective, "0.0.0.0/0") && !contains(local.client_cidrs_effective, "::/0") &&
      alltrue([for r in aws_vpc_security_group_ingress_rule.vpce_from_runtime : r.cidr_ipv4 == null && r.cidr_ipv6 == null]) &&
    alltrue([for r in aws_vpc_security_group_ingress_rule.existing_endpoint_from_runtime : r.cidr_ipv4 == null && r.cidr_ipv6 == null]))
    error_message = "no security group ingress from 0.0.0.0/0 or ::/0"
  }
}

# D2: datastore non-exposure, public mode (AC 26): no Memory resource policy, no ingress rules.
run "d2_datastores_not_exposed_public" {
  command = plan

  # The mock leaves these ARNs unknown at plan; pin them so the rendered policies are known.
  override_resource {
    target          = aws_s3_bucket.kb
    override_during = plan
    values = {
      arn = "arn:aws:s3:::gactest-kb-example"
    }
  }
  override_resource {
    target          = aws_s3_bucket.kb_logs
    override_during = plan
    values = {
      arn = "arn:aws:s3:::gactest-kb-logs-example"
    }
  }
  override_resource {
    target          = aws_iam_role.runtime_exec
    override_during = plan
    values = {
      arn = "arn:aws:iam::123456789012:role/gactest-runtime-exec-role"
    }
  }

  variables {
    network_access              = "public"
    acknowledge_public_endpoint = true
    create_vpc                  = false
  }

  assert {
    condition = alltrue([for b in [aws_s3_bucket_public_access_block.kb, aws_s3_bucket_public_access_block.kb_logs] :
    b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets])
    error_message = "both KB buckets block public access (all four flags)"
  }
  assert {
    condition = alltrue([for s in concat(jsondecode(aws_s3_bucket_policy.kb.policy).Statement, jsondecode(aws_s3_bucket_policy.kb_logs.policy).Statement, jsondecode(local.ecr_repo_policy).Statement) :
    try(s.Condition, null) != null if contains([jsonencode("*"), jsonencode({ AWS = "*" })], jsonencode(s.Principal))])
    error_message = "no bucket or ECR policy grants an unconditioned Principal *"
  }
  assert {
    condition     = length(data.aws_iam_policy_document.memory_resource) == 0 && length(aws_bedrockagentcore_resource_policy.memory) == 0
    error_message = "public mode: Memory is IAM-only (no resource policy)"
  }
  assert {
    condition     = contains([for s in jsondecode(aws_s3_bucket_policy.kb.policy).Statement : s.Sid if s.Effect == "Deny" && s.Condition == { Bool = { "aws:SecureTransport" = "false" } }], "DenyInsecureTransport")
    error_message = "the KB bucket policy keeps its TLS-only deny"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.vpce_from_runtime) == 0 && length(aws_vpc_security_group_ingress_rule.existing_endpoint_from_runtime) == 0
    error_message = "public mode creates no security group ingress"
  }
}
