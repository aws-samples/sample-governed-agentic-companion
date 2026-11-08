# Terraform + provider version constraints for the Governed Agentic Companion AgentCore
# deployment.
#
# hashicorp/aws carries the native AgentCore Runtime resource
# (aws_bedrockagentcore_agent_runtime) plus ECR, IAM, S3, and Cognito, and (v6.66+) the native
# AgentCore Gateway + OAuth2 credential provider + gateway target used in gateway.tf.
#
# Private network mode needs no provider raise: everything it uses exists at or below 6.66.0
# (aws_bedrockagentcore_resource_policy since 6.47.0; gateway target gateway_iam_role
# service/region since 6.48.0; the runtime's computed network_mode_config
# require_service_s3_endpoint since 6.55.0; aws_vpc_endpoint, aws_vpc_security_group_*_rule,
# aws_iam_session_context and aws_ec2_managed_prefix_list are long-standing).
#
# .terraform.lock.hcl is gitignored, so each provider carries a pessimistic constraint (same
# major, never older than the version the kit was tested with). That bounds selection within v6;
# it does not pin checksums (docs/SECURITY.md SD-28). `terraform init` writes a local lock.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66" # >= 6.66.0 (native AgentCore gateway resources), < 7.0.0
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8" # archive_file builds the interceptor and probe zips
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Backend intentionally left unconfigured so this is safe to `init` locally. For real
  # engagements, copy backend.tf.example to backend.tf (S3 backend with native locking).
}

provider "aws" {
  region = var.aws_region

  # A human runs `terraform apply` with their own credentials/profile (Tenet 1: no agent
  # deploys). Set AWS_PROFILE / AWS_REGION in the environment, or pass -var aws_region=...
  default_tags {
    tags = var.tags
  }
}
