# ebanking.amalbankso.com failover. One fixed-endpoint TCP health check per
# endpoint. Each health check targets the IP directly, never the failover
# hostname itself.
#
# Behaviour to be aware of (see docs/MIGRATION_PLAN.md, section 5):
#  * By default only the PRIMARY record is gated by a health check (see
#    secondary_failover_requires_health_check). With health checks on both records Route 53 returns the PRIMARY when both
#    are unhealthy, and returns to it as soon as it recovers.
#  * TCP 443 proves reachability only, not application readiness.

resource "aws_route53_health_check" "ebanking_primary" {
  count = var.enable_ebanking_failover ? 1 : 0

  type              = "TCP"
  ip_address        = var.ebanking_primary_ip
  port              = var.health_check_port
  request_interval  = var.health_check_interval
  failure_threshold = var.health_check_failure_threshold
  measure_latency   = false

  tags = {
    Name = "ebanking-primary-${var.ebanking_primary_ip}-tcp${var.health_check_port}"
    Role = "primary"
  }
}

resource "aws_route53_health_check" "ebanking_secondary" {
  count = var.enable_ebanking_failover ? 1 : 0

  type              = "TCP"
  ip_address        = var.ebanking_secondary_ip
  port              = var.health_check_port
  request_interval  = var.health_check_interval
  failure_threshold = var.health_check_failure_threshold
  measure_latency   = false

  tags = {
    Name = "ebanking-secondary-${var.ebanking_secondary_ip}-tcp${var.health_check_port}"
    Role = "secondary"
  }
}

resource "aws_route53_record" "ebanking_primary" {
  count = var.enable_ebanking_failover ? 1 : 0

  zone_id         = aws_route53_zone.ebanking.zone_id
  name            = local.ebanking_zone
  type            = "A"
  ttl             = var.ebanking_ttl
  records         = [var.ebanking_primary_ip]
  set_identifier  = "ebanking-primary"
  health_check_id = aws_route53_health_check.ebanking_primary[0].id

  failover_routing_policy {
    type = "PRIMARY"
  }
}

resource "aws_route53_record" "ebanking_secondary" {
  count = var.enable_ebanking_failover ? 1 : 0

  zone_id         = aws_route53_zone.ebanking.zone_id
  name            = local.ebanking_zone
  type            = "A"
  ttl             = var.ebanking_ttl
  records         = [var.ebanking_secondary_ip]
  set_identifier  = "ebanking-secondary"
  health_check_id = var.secondary_failover_requires_health_check ? aws_route53_health_check.ebanking_secondary[0].id : null

  failover_routing_policy {
    type = "SECONDARY"
  }
}
