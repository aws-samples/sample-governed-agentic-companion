terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # data.aws_region.current.region is a v6-only attribute
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}
