# Redirect health: alert the shared topic if CloudFront starts returning 5xx.
# (Route 53 health checks cannot test a redirect; the Function answers every
# request itself, so any 5xx means the distribution or Function is failing.)

data "aws_sns_topic" "alerts" {
  count = var.enable_alarms && var.enable_redirect && var.enable_redirect_distribution ? 1 : 0
  name  = "${var.name_prefix}-alerts"
}

resource "aws_cloudwatch_metric_alarm" "redirect_5xx" {
  count = var.enable_alarms && var.enable_redirect && var.enable_redirect_distribution ? 1 : 0

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
