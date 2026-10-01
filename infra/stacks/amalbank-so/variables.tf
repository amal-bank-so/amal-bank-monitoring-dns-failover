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

variable "inventory_file" {
  description = "Inventory JSON, relative to this stack. Overridden only by the Terraform tests."
  type        = string
  default     = "inventory/amalbank.so.json"
}

# --- amalbank.so redirect ----------------------------------------------------

variable "enable_certificate" {
  description = "Request the ACM certificate and create its validation CNAMEs in this zone. ACM validates only after the zone is publicly delegated to Route 53 and abandons a request still pending after 72 hours, so request it shortly before delegating."
  type        = bool
  default     = false
}

variable "enable_redirect" {
  description = "Create the CloudFront redirect Function. Needs the verified redirect behaviour (status, path, query), which has no default."
  type        = bool
  default     = false
}

variable "enable_redirect_distribution" {
  description = "Wait for the certificate to be ISSUED (needs the zone delegated), then create the CloudFront distribution. Requires enable_certificate and enable_redirect. It serves on its own cloudfront.net name and can be tested before any DNS record points at it."
  type        = bool
  default     = false
}

variable "web_use_cloudfront" {
  description = "Switch the apex/www records from the legacy A records to CloudFront aliases (in place). Requires enable_redirect_distribution. Set false again to roll back to the legacy IP."
  type        = bool
  default     = false
}

# --- web records: legacy (parity) -> CloudFront ------------------------------

variable "legacy_web_ips" {
  description = "The apex/www A record value(s) currently served by the old provider's redirect service, taken from the verified export (observed publicly: 34.198.182.201). Served at the apex and www until the CloudFront distribution is enabled, so delegating the zone changes nothing for web visitors. They are excluded from the record inventory (bind_to_inventory.py --exclude) and managed in web.tf."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for ip in var.legacy_web_ips : can(cidrhost("${ip}/32", 0))])
    error_message = "legacy_web_ips must be IPv4 addresses."
  }
}

variable "legacy_web_ttl" {
  description = "TTL of the legacy apex/www A records (observed: 60)."
  type        = number
  default     = 60
}

variable "no_web_records" {
  description = "Explicitly acknowledge that the apex and www should have NO address record before the redirect distribution exists. Without this (or legacy_web_ips) a verified inventory refuses to plan, because delegating would take the website down."
  type        = bool
  default     = false
}

variable "apex_ns_ttl" {
  description = "TTL of the zone's apex NS records. Route 53 defaults to 172800 (2 days), which would keep resolvers on this delegation for days after a rollback; lowered so a rollback takes effect in minutes. The name servers themselves are the Route 53 generated ones."
  type        = number
  default     = 900

  validation {
    condition     = var.apex_ns_ttl >= 60 && var.apex_ns_ttl <= 172800
    error_message = "apex_ns_ttl must be between 60 and 172800."
  }
}

variable "enable_web_health_check" {
  description = "Route 53 HTTP health check against http://amalbank.so/ (any 2xx/3xx is healthy) with an alarm to the shared topic. Observes whatever currently serves the name, legacy or CloudFront. Needs enable_alarms."
  type        = bool
  default     = true
}

variable "nxdomain_alarm_threshold" {
  description = "NXDOMAIN answers in 5 minutes that raise an alarm. After delegation this reveals names that were expected to exist but are missing from the zone. Internet scanners cause a low background rate, so keep it above that."
  type        = number
  default     = 10
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
