variable "aws_region" {
  description = "Must be us-east-1 (CloudFront ACM certificate)."
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

# --- amalbank.so inventory ---------------------------------------------------
# The verified record inventory lives in inventory/amalbank.so.json. Records are
# only created when that file says "verified": true (see records.tf).

# --- amalbank.so redirect ----------------------------------------------------

variable "enable_redirect" {
  description = "Stage A: create the ACM certificate (+ validation records) and the CloudFront redirect Function."
  type        = bool
  default     = false
}

variable "enable_redirect_distribution" {
  description = "Stage B: wait for the certificate to be ISSUED, then create the CloudFront distribution and Route 53 aliases. Requires enable_redirect. Only set once the certificate validation records are resolvable (zone delegated, or the records added at the live DNS provider)."
  type        = bool
  default     = false
}

variable "redirect_wildcard" {
  description = "false (default) mirrors live behaviour: redirect the apex and www only. true redirects the apex and *.amalbank.so instead (one label deep; covers www). A wildcard changes behaviour: unknown names stop returning NXDOMAIN."
  type        = bool
  default     = false
}

variable "redirect_target" {
  description = "Redirect destination origin, no trailing slash."
  type        = string
  default     = "https://www.amalbankso.so"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+$", var.redirect_target))
    error_message = "redirect_target must be an https:// origin with no path or trailing slash."
  }
}

# The following three are deliberately without a default: they must be set from
# the live behaviour captured in Phase 0 (task 0.7), not assumed.
variable "redirect_status_code" {
  description = "HTTP status of the redirect (301, 302, 307 or 308), from the verified live behaviour."
  type        = number
  default     = null

  validation {
    condition     = var.redirect_status_code == null ? true : contains([301, 302, 307, 308], var.redirect_status_code)
    error_message = "redirect_status_code must be 301, 302, 307 or 308."
  }
}

variable "redirect_preserve_path" {
  description = "Append the request path to the target (true) or always send to the target root (false)."
  type        = bool
  default     = null
}

variable "redirect_preserve_query" {
  description = "Forward the query string to the target."
  type        = bool
  default     = null
}

variable "redirect_cache_max_age" {
  description = "Cache-Control max-age (seconds) on redirect responses. Kept short so rollback and corrections take effect quickly."
  type        = number
  default     = 300
}

variable "redirect_price_class" {
  description = "CloudFront price class. PriceClass_All gives the best latency for viewers outside NA/EU; cost for redirects is negligible."
  type        = string
  default     = "PriceClass_All"
}
