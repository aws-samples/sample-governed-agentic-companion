# Terraform + provider version constraints for the Governed Agentic Companion AgentCore
# deployment.
#
# hashicorp/aws carries the native AgentCore Runtime resource
# (aws_bedrockagentcore_agent_runtime) plus ECR, IAM, S3, and Cognito, and (v6.66+) the native
# AgentCore Gateway + OAuth2 credential provider + gateway target used in gateway.tf.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2.0"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }

  # Backend intentionally left unconfigured so this is safe to `init` locally. For real
  # engagements, add a backend.tf with an S3 backend + DynamoDB lock table (see README).
}

provider "aws" {
  region = var.aws_region

  # A human runs `terraform apply` with their own credentials/profile (Tenet 1: no agent
  # deploys). Set AWS_PROFILE / AWS_REGION in the environment, or pass -var aws_region=...
  default_tags {
    tags = var.tags
  }
}
