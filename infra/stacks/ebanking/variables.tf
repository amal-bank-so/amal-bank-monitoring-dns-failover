variable "aws_region" {
  description = "Must be us-east-1 (Route 53 health-check metrics are only published there)."
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
  description = "Prefix for named resources. Must match the shared stack (the alert topic is found by name)."
  type        = string
  default     = "amal-dns"
}

variable "extra_tags" {
  description = "Additional tags applied to every resource (e.g. CostCenter, Owner)."
  type        = map(string)
  default     = {}
}


variable "enable_alarms" {
  description = "Create alarms that notify the shared alert topic. Requires the shared stack to be applied first."
  type        = bool
  default     = true
}

variable "query_log_retention_days" {
  description = "Retention for Route 53 query logs. Confirm against the bank's retention policy."
  type        = number
  default     = 365

  validation {
    condition     = contains([30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.query_log_retention_days)
    error_message = "query_log_retention_days must be a CloudWatch Logs retention value."
  }
}

# --- ebanking failover -------------------------------------------------------

variable "enable_ebanking_failover" {
  description = "Create the failover records and health checks in the ebanking zone."
  type        = bool
  default     = true
}

variable "ebanking_primary_ip" {
  description = "Primary endpoint (observed: Failover Location 1)."
  type        = string
  default     = "37.34.133.35"

  validation {
    condition     = can(cidrhost("${var.ebanking_primary_ip}/32", 0))
    error_message = "ebanking_primary_ip must be an IPv4 address."
  }
}

variable "ebanking_secondary_ip" {
  description = "Secondary endpoint (observed: Failover Location 2)."
  type        = string
  default     = "91.140.155.171"

  validation {
    condition     = can(cidrhost("${var.ebanking_secondary_ip}/32", 0))
    error_message = "ebanking_secondary_ip must be an IPv4 address."
  }
}

variable "ebanking_ttl" {
  description = "TTL of the ebanking failover records. Starts at the observed 1800 to preserve behaviour; lower to 300 for cutover prep and to the validated final value (proposed 60) afterwards."
  type        = number
  default     = 1800

  validation {
    condition     = var.ebanking_ttl >= 30 && var.ebanking_ttl <= 86400
    error_message = "ebanking_ttl must be between 30 and 86400 seconds."
  }
}

variable "health_check_port" {
  description = "TCP port monitored (observed: 443)."
  type        = number
  default     = 443
}

variable "health_check_interval" {
  description = "Seconds between checks: 10 or 30. Provisional (30); validate against current DigiCert behaviour."
  type        = number
  default     = 30

  validation {
    condition     = contains([10, 30], var.health_check_interval)
    error_message = "health_check_interval must be 10 or 30."
  }
}

variable "health_check_failure_threshold" {
  description = "Consecutive failures before unhealthy. Provisional (3)."
  type        = number
  default     = 3

  validation {
    condition     = var.health_check_failure_threshold >= 1 && var.health_check_failure_threshold <= 10
    error_message = "health_check_failure_threshold must be between 1 and 10."
  }
}

variable "enable_failover_test" {
  description = "Create an isolated failover-test.<ebanking zone> record pair on TEST-NET addresses whose health is driven by CloudWatch alarms, so failover/failback/both-down can be exercised without touching live endpoints."
  type        = bool
  default     = false
}
