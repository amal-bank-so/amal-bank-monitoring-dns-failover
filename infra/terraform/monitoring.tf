# Health alarms -> SNS -> owner-approved email recipients.
# Email subscriptions stay "PendingConfirmation" until each recipient clicks the
# confirmation link; delivery must be verified before cutover (plan task 2.3).
# The topic is not KMS-encrypted: CloudWatch alarms cannot publish to topics
# encrypted with the AWS-managed SNS key. Use a customer-managed key policy if
# the bank requires encryption at rest.

data "aws_caller_identity" "current" {}

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "AllowCloudWatchAlarms"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  health_checks = var.enable_ebanking_failover ? {
    primary   = aws_route53_health_check.ebanking_primary[0].id
    secondary = aws_route53_health_check.ebanking_secondary[0].id
  } : {}
}

# HealthCheckStatus is 1 (healthy) or 0 (unhealthy). Missing data means the
# checkers are not reporting, which is treated as a failure.
resource "aws_cloudwatch_metric_alarm" "ebanking_health" {
  for_each = local.health_checks

  alarm_name          = "${var.name_prefix}-ebanking-${each.key}-unhealthy"
  alarm_description   = "Route 53 health check for the ebanking ${each.key} endpoint is unhealthy (TCP ${var.health_check_port})."
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = each.value }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# Both endpoints unhealthy at the same time: critical (Route 53 then answers with the primary).
resource "aws_cloudwatch_composite_alarm" "ebanking_both_down" {
  count = var.enable_ebanking_failover ? 1 : 0

  alarm_name        = "${var.name_prefix}-ebanking-both-unhealthy"
  alarm_description = "Both ebanking endpoints are failing Route 53 health checks. Route 53 will answer with the primary."
  alarm_rule        = "ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["primary"].alarm_name}) AND ALARM(${aws_cloudwatch_metric_alarm.ebanking_health["secondary"].alarm_name})"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
