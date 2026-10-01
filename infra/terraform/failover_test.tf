# Isolated failover simulation. Creates failover-test.<ebanking zone> with
# PRIMARY/SECONDARY records on TEST-NET-1 (RFC 5737) addresses that no live
# service uses. Each record's health check follows a CloudWatch alarm, so
# health states are set by publishing a custom metric (or forcing alarm state)
# instead of ever disabling a live banking endpoint:
#   aws cloudwatch put-metric-data --namespace AmalDnsTest --metric-name Unhealthy \
#     --dimensions Target=primary --value 1 --storage-resolution 1
# (tools/failover_test.py does this and checks every answer.) If the driver stops
# publishing, the data is "missing" and treated as healthy, so the test pair returns
# to normal on its own.
# Answers are checked with route53:TestDNSAnswer / direct queries to the zone's
# AWS name servers (the zone need not be delegated).

locals {
  failover_test_name = "failover-test.${local.ebanking_zone}"
  failover_test_targets = var.enable_failover_test ? {
    primary   = { ip = "192.0.2.10", type = "PRIMARY" }
    secondary = { ip = "192.0.2.20", type = "SECONDARY" }
  } : {}
}

resource "aws_cloudwatch_metric_alarm" "failover_test" {
  for_each = local.failover_test_targets

  alarm_name          = "${var.name_prefix}-failover-test-${each.key}"
  alarm_description   = "Simulated health for the failover test ${each.key} target. Alarm = unhealthy."
  namespace           = "AmalDnsTest"
  metric_name         = "Unhealthy"
  dimensions          = { Target = each.key }
  statistic           = "Maximum"
  period              = 10 # high-resolution alarm: the driver publishes StorageResolution=1 datapoints so state changes take effect in seconds, not minutes
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  # Deliberately no alarm_actions: test state changes must not page anyone.
}

resource "aws_route53_health_check" "failover_test" {
  for_each = local.failover_test_targets

  type                            = "CLOUDWATCH_METRIC"
  cloudwatch_alarm_name           = aws_cloudwatch_metric_alarm.failover_test[each.key].alarm_name
  cloudwatch_alarm_region         = var.aws_region
  insufficient_data_health_status = "Healthy"

  tags = {
    Name = "failover-test-${each.key}"
    Role = "test"
  }
}

resource "aws_route53_record" "failover_test" {
  for_each = local.failover_test_targets

  zone_id         = aws_route53_zone.ebanking.zone_id
  name            = local.failover_test_name
  type            = "A"
  ttl             = 60
  records         = [each.value.ip]
  set_identifier  = "failover-test-${each.key}"
  health_check_id = aws_route53_health_check.failover_test[each.key].id

  failover_routing_policy {
    type = each.value.type
  }
}
