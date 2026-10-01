# Monitoring for amalbank.so, all notifying the shared alert topic (shared stack,
# apply it first). Set enable_alarms = false to plan before that topic exists.

locals {
  alarms_enabled = var.enable_alarms
}

data "aws_sns_topic" "alerts" {
  count = local.alarms_enabled ? 1 : 0
  name  = "${var.name_prefix}-alerts"
}

# --- Website: does http://amalbank.so/ answer? --------------------------------------
# Whatever currently serves the name, legacy redirect service or CloudFront, is
# observed end to end through public DNS. 2xx and 3xx count as healthy.
resource "aws_route53_health_check" "web_http" {
  count = var.enable_web_health_check ? 1 : 0

  type              = "HTTP"
  fqdn              = local.amalbank_zone
  port              = 80
  resource_path     = "/"
  request_interval  = 30
  failure_threshold = 3
  measure_latency   = false

  tags = {
    Name = "amalbank-so-web-http"
  }
}

resource "aws_cloudwatch_metric_alarm" "web_http" {
  count = var.enable_web_health_check && local.alarms_enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-amalbank-so-web-unhealthy"
  alarm_description   = "http://amalbank.so/ is not answering with 2xx/3xx according to Route 53 health checkers."
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = aws_route53_health_check.web_http[0].id }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
  ok_actions    = [data.aws_sns_topic.alerts[0].arn]
}

# --- Completeness safety net: NXDOMAIN answers ---------------------------------------
# The zone was built without an authoritative export of the previous provider, so a
# record that existed there but not here would show up as NXDOMAIN/NODATA queries after
# delegation. Query-log lines are space-delimited:
#   version timestamp zone_id query_name query_type response_code protocol edge resolver ecs
resource "aws_cloudwatch_log_metric_filter" "nxdomain" {
  name           = "${var.name_prefix}-amalbank-so-nxdomain"
  log_group_name = aws_cloudwatch_log_group.query.name
  pattern        = "[version, timestamp, zone_id, query_name, query_type, response_code=NXDOMAIN, ...]"

  metric_transformation {
    name          = "NxdomainAnswers"
    namespace     = "AmalDns/amalbank.so"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "nxdomain" {
  count = local.alarms_enabled ? 1 : 0

  alarm_name          = "${var.name_prefix}-amalbank-so-nxdomain"
  alarm_description   = "Route 53 answered NXDOMAIN for amalbank.so names at least ${var.nxdomain_alarm_threshold} times in 5 minutes. After delegation this can mean a record that existed at the previous provider is missing here. Check the query log (log group ${aws_cloudwatch_log_group.query.name}) for the names."
  namespace           = "AmalDns/amalbank.so"
  metric_name         = "NxdomainAnswers"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.nxdomain_alarm_threshold
  treat_missing_data  = "notBreaching"

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
  ok_actions    = [data.aws_sns_topic.alerts[0].arn]

  depends_on = [aws_cloudwatch_log_metric_filter.nxdomain]
}

# --- Redirect distribution errors ----------------------------------------------------
# The Function answers every request itself, so any 5xx means the distribution or the
# Function is failing.
resource "aws_cloudwatch_metric_alarm" "redirect_5xx" {
  count = local.alarms_enabled && var.enable_redirect_distribution ? 1 : 0

  alarm_name          = "${var.name_prefix}-amalbank-so-redirect-5xx"
  alarm_description   = "CloudFront 5xx error rate above 5% for the amalbank.so redirect distribution."
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  dimensions          = { DistributionId = aws_cloudfront_distribution.redirect[0].id, Region = "Global" }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching" # no traffic is not a failure

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
  ok_actions    = [data.aws_sns_topic.alerts[0].arn]
}
