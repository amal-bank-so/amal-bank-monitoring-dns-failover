# Health alarms -> shared SNS topic -> owner-approved email recipients.
# The topic and subscriptions live in the shared stack (apply that first).

data "aws_sns_topic" "alerts" {
  count = var.enable_alarms && var.enable_ebanking_failover ? 1 : 0
  name  = "${var.name_prefix}-alerts"
}

locals {
  health_checks = var.enable_ebanking_failover ? {
    primary   = aws_route53_health_check.ebanking_primary[0].id
    secondary = aws_route53_health_check.ebanking_secondary[0].id
  } : {}
  alarms_enabled = var.enable_alarms && var.enable_ebanking_failover
}

# HealthCheckStatus is 1 (healthy) or 0 (unhealthy). Missing data means the
# checkers are not reporting, which is treated as a failure.
resource "aws_cloudwatch_metric_alarm" "ebanking_health" {
  for_each = local.alarms_enabled ? local.health_checks : {}

  alarm_name          = "${var.name_prefix}-ebanking-${each.key}-unhealthy"
  alarm_description   = "Route 53 health check for the ebanking ${each.key} endpoint (${each.key == "primary" ? "${var.ebanking_primary_name} ${var.ebanking_primary_ip}" : "${var.ebanking_secondary_name} ${var.ebanking_secondary_ip}"}) is unhealthy (TCP ${var.health_check_port})."
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = each.value }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
  ok_actions    = [data.aws_sns_topic.alerts[0].arn]
}

# Both endpoints unhealthy at the same time: critical (Route 53 then answers with the primary).
resource "aws_cloudwatch_composite_alarm" "ebanking_both_down" {
  count = local.alarms_enabled ? 1 : 0

  alarm_name        = "${var.name_prefix}-ebanking-both-unhealthy"
  alarm_description = "Both ebanking endpoints (${var.ebanking_primary_name} and ${var.ebanking_secondary_name}) are failing Route 53 health checks. Route 53 will answer with the primary (${var.ebanking_primary_name})."
  alarm_rule        = "ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["primary"].alarm_name}) AND ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["secondary"].alarm_name})"

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
  ok_actions    = [data.aws_sns_topic.alerts[0].arn]
}

# Failover state: the primary is failing and the secondary is healthy, i.e. Route 53 is answering with the secondary. Its ALARM
# state sends the "HIGH - Failover from Primary to Secondary" email (the shared stack's notify Lambda decides what to send).
# If the secondary is unhealthy too this alarm stays OK and the both-unhealthy alarm (CRITICAL) takes over. "Primary is Back" is
# the primary-unhealthy alarm returning to OK.
resource "aws_cloudwatch_composite_alarm" "ebanking_failover" {
  count = local.alarms_enabled ? 1 : 0

  alarm_name        = "${var.name_prefix}-ebanking-failover"
  alarm_description = "Failover: ${var.ebanking_primary_name} ${var.ebanking_primary_ip} is failing its health check and ${var.ebanking_secondary_name} ${var.ebanking_secondary_ip} is healthy; Route 53 answers with the secondary."
  alarm_rule        = "ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["primary"].alarm_name}) AND NOT ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["secondary"].alarm_name})"

  alarm_actions = [data.aws_sns_topic.alerts[0].arn]
}

