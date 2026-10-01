# Identical in every stack (checked by tools/test_stack_layout.py).
# Everything lives in us-east-1: CloudFront certificates must be issued there,
# and Route 53 health-check metrics are only published to CloudWatch there.
provider "aws" {
  region = var.aws_region

  # Refuse to run against any other account.
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = merge(
      {
        Project   = "amal-dns-migration"
        ManagedBy = "terraform"
        Component = "dns"
        Stack     = local.stack_name
      },
      var.extra_tags,
    )
  }
}
