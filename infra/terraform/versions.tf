terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: supply bucket/key/region with
  #   terraform init -backend-config=backend.hcl
  # (see backend.hcl.example). For offline validation use `init -backend=false`.
  backend "s3" {}
}
