output "ebanking_zone" {
  description = "Hosted zone ID and the exact four Route 53 name servers for the ebanking child NS records in amalbankso.com."
  value = {
    zone_id      = aws_route53_zone.ebanking.zone_id
    name_servers = sort(aws_route53_zone.ebanking.name_servers)
  }
}

output "ebanking_health_check_ids" {
  value = local.health_checks
}

output "query_log_group" {
  value = aws_cloudwatch_log_group.query.name
}

output "failover_test_name" {
  value = var.enable_failover_test ? local.failover_test_name : null
}
