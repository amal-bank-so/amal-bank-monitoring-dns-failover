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

# --- notifications (SendGrid) --------------------------------------------------

variable "notification_recipients" {
  description = "Email addresses that receive every alarm notification, sent through SendGrid by the notify Lambda. More can be added without a deployment through the optional SENDGRID_TO_EMAILS field (comma separated) of the secret."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.notification_recipients : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "notification_recipients must be valid email addresses."
  }
}

variable "sendgrid_secret_arn" {
  description = "ARN of the AWS Secrets Manager secret holding SENDGRID_API_KEY and SENDGRID_FROM_EMAIL (it may live in another region). The notify Lambda's role can read only this secret."
  type        = string
}

variable "ebanking_primary_label" {
  description = "How the primary e-banking endpoint is named in notification emails."
  type        = string
  default     = "Primary (Zain)"
}

variable "ebanking_secondary_label" {
  description = "How the secondary e-banking endpoint is named in notification emails."
  type        = string
  default     = "Secondary (FastTelco)"
}

variable "notification_subject_prefix" {
  description = "Prefix of every notification email subject."
  type        = string
  default     = "[Amal DNS] "
}

variable "notification_log_retention_days" {
  description = "Retention of the notify Lambda's logs."
  type        = number
  default     = 365
}
