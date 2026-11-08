# Terraform and provider constraints for the platform (shared layer) stack.
#
# ~> 6.68 (>= 6.68.0, < 7.0.0): the shared-identity and private-network modules read
# data.aws_region.current.region, a v6-only attribute, and an adopted state is never planned with
# an older provider than the 6.68.0 that wrote it. .terraform.lock.hcl is gitignored, so this
# constraint bounds provider selection within v6; it does not pin checksums (docs/SECURITY.md
# SD-28). `terraform init` writes a local lock.
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
  # No backend block: the first apply uses local state (the bucket does not exist yet). Then copy
  # backend.tf.example to backend.tf (gitignored) and run `terraform init -migrate-state`.
}

provider "aws" {
  region = var.aws_region
  # A human runs apply with their own credentials (Tenet 1). An empty map adds no tags, which
  # keeps an adoption plan free of tag diffs.
  default_tags {
    tags = var.tags
  }
}
