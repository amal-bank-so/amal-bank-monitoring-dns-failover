variable "aws_region" {
  description = "Must be us-east-1 (CloudFront ACM certificate and Route 53 health-check metrics)."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "aws_region must be us-east-1."
  }
}

variable "expected_account_id" {
  description = "Terraform refuses to run against any other AWS account."
  type        = string
  default     = "029288159395"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit AWS account ID."
  }
}

variable "name_prefix" {
  description = "Prefix for named resources. Must be the same in every stack (the alert topic is found by name)."
  type        = string
  default     = "amal-dns"
}

variable "extra_tags" {
  description = "Additional tags applied to every resource (e.g. CostCenter, Owner)."
  type        = map(string)
  default     = {}
}

variable "alert_emails" {
  description = "Owner-approved alert recipients. Each address must confirm the SNS subscription email before it receives alerts. Empty = topic only."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.alert_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "alert_emails must be valid email addresses."
  }
}
