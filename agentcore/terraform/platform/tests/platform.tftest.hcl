# Plan-only tests for the platform stack (Terraform >= 1.7). Mocked provider: no AWS calls and no
# credentials. Run from agentcore/terraform/platform: `terraform init -backend=false && terraform test`.

mock_provider "aws" {
  # Computed values are known at plan so assertions can read them.
  override_during = plan

  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id = "ap-southeast-2_Example01"
    }
  }
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }
  mock_data "aws_availability_zones" {
    defaults = {
      zone_ids = ["apse2-az1", "apse2-az2", "apse2-az3"]
    }
  }
  # A syntactically valid policy JSON (never asserted on).
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:role/placeholder"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "ap-southeast-2"
      name   = "ap-southeast-2"
    }
  }
}

variables {
  aws_region        = "ap-southeast-2"
  state_bucket_name = "companion-tfstate-123456789012-ap-southeast-2"
  apps = {
    gac   = "Governed Agentic Companion"
    other = "Another companion"
  }
}

# P1: identity and backend only (the adoption shape).
run "p1_identity_and_backend_only" {
  command = plan

  assert {
    condition     = output.existing_oidc_issuer == "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_Example01"
    error_message = "existing_oidc_issuer must be the shared pool issuer"
  }

  assert {
    condition     = length(module.identity.app_groups) == 2
    error_message = "one entitlement group per app"
  }

  assert {
    condition     = module.backend.lock_table == ""
    error_message = "the lock table is off by default (S3 native locking)"
  }

  assert {
    condition     = length(module.network) == 0 && length(terraform_data.platform_guards) == 0
    error_message = "with both toggles off the platform holds only backend and identity"
  }

  assert {
    condition     = output.vpc_id == "" && length(output.existing_interface_endpoint_ids) == 0
    error_message = "network outputs are empty when the network is off"
  }
}

# P0: an app key that is not companion-shaped is refused (passing twin: p1).
run "p0_bad_app_key_refused" {
  command = plan

  variables {
    apps = { "Customer_Name" = "x" }
  }

  expect_failures = [var.apps]
}

# P2: shared network on with client CIDRs (passing twin of P3).
run "p2_shared_network_on" {
  command = plan

  variables {
    enable_private_network = true
    private_client_cidrs   = ["10.0.0.0/16"]
  }

  assert {
    condition     = length(module.network) == 1 && length(terraform_data.platform_guards) == 1
    error_message = "network and guards are created"
  }
  assert {
    condition     = toset(keys(output.existing_interface_endpoint_ids)) == toset(["bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "xray"])
    error_message = "default shared endpoint set"
  }
  assert {
    condition = alltrue([for k in ["manage_identity_pool", "existing_user_pool_id", "existing_oidc_issuer", "network_access", "create_vpc",
      "manage_existing_endpoint_ingress", "vpc_id", "private_subnet_ids", "private_route_table_ids", "existing_s3_gateway_endpoint_id",
      "existing_interface_endpoint_ids", "existing_endpoint_security_group_ids", "existing_client_endpoint_security_group_ids",
    "private_client_cidrs"] : contains(keys(output.companion_consumer_inputs), k)])
    error_message = "companion_consumer_inputs keys"
  }
  assert {
    condition     = length(output.private_subnet_ids) == 2 && output.private_client_cidrs == tolist(["10.0.0.0/16"])
    error_message = "two subnets; client CIDRs passed through"
  }
  assert {
    condition     = length(module.network[0].resolver_client_cidrs) == 0
    error_message = "resolver off by default"
  }
}

# P3: network on without client CIDRs is refused (passing twin: P2).
run "p3_network_without_client_cidrs_refused" {
  command = plan

  variables {
    enable_private_network = true
  }

  expect_failures = [terraform_data.platform_guards]
}

# P3b: resolver on with the network off is refused (passing twin: P2 with the resolver, P6).
run "p3b_resolver_without_network_refused" {
  command = plan

  variables {
    enable_resolver_inbound_endpoint = true
  }

  expect_failures = [terraform_data.platform_guards]
}

# P4: an account-wide KB pattern is rejected (passing twin: P4b).
run "p4_kb_pattern_all_buckets_rejected" {
  command = plan

  variables {
    kb_bucket_arn_patterns = ["arn:aws:s3:::*"]
  }

  expect_failures = [var.kb_bucket_arn_patterns]
}

# P4b: an exact custom bucket name without -kb- is admitted (N6).
run "p4b_kb_exact_custom_bucket_admitted" {
  command = plan

  variables {
    enable_private_network = true
    private_client_cidrs   = ["10.0.0.0/16"]
    kb_bucket_arn_patterns = ["arn:aws:s3:::team-knowledge-store"]
  }

  assert {
    condition     = length(module.network) == 1
    error_message = "exact custom bucket ARN plans"
  }
}

# P4c: a short leading-wildcard pattern is rejected (passing twin: P4b).
run "p4c_kb_short_wildcard_rejected" {
  command = plan

  variables {
    kb_bucket_arn_patterns = ["arn:aws:s3:::a*"]
  }

  expect_failures = [var.kb_bucket_arn_patterns]
}

# P4d: a malformed value fails validation cleanly (no evaluation error from split()).
run "p4d_kb_malformed_rejected" {
  command = plan

  variables {
    kb_bucket_arn_patterns = ["not-an-arn"]
  }

  expect_failures = [var.kb_bucket_arn_patterns]
}

# P5: the client SG output is one element of a two-element endpoint SG list.
run "p5_client_sg_subset" {
  command = plan

  variables {
    enable_private_network = true
    private_client_cidrs   = ["10.0.0.0/16"]
  }

  assert {
    condition     = length(output.existing_client_endpoint_security_group_ids) == 1 && length(output.existing_endpoint_security_group_ids) == 2
    error_message = "one client SG; two SGs in total"
  }
  # Membership of the mocked SG id is unknown at plan (security group ids are computed), so this
  # case asserts lengths only; the static test checks the output indexes the client SG only.
}

# P6: shared network with the resolver on (twin of P3b).
run "p6_shared_network_with_resolver" {
  command = plan

  variables {
    enable_private_network           = true
    private_client_cidrs             = ["10.0.0.0/16"]
    enable_resolver_inbound_endpoint = true
  }

  assert {
    condition     = toset(module.network[0].resolver_client_cidrs) == toset(["10.0.0.0/16"])
    error_message = "resolver DNS rules use the client CIDRs"
  }
}
