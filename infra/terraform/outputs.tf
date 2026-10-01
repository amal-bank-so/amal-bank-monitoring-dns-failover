output "amalbank_so_zone" {
  description = "Hosted zone ID and the exact four Route 53 name servers for the amalbank.so registrar delegation."
  value = {
    zone_id      = aws_route53_zone.amalbank_so.zone_id
    name_servers = sort(aws_route53_zone.amalbank_so.name_servers)
  }
}

output "ebanking_zone" {
  description = "Hosted zone ID and the exact four Route 53 name servers for the ebanking child NS records in amalbankso.com."
  value = {
    zone_id      = aws_route53_zone.ebanking.zone_id
    name_servers = sort(aws_route53_zone.ebanking.name_servers)
  }
}

output "ebanking_health_check_ids" {
  value = var.enable_ebanking_failover ? {
    primary   = aws_route53_health_check.ebanking_primary[0].id
    secondary = aws_route53_health_check.ebanking_secondary[0].id
  } : {}
}

output "acm_validation_records" {
  description = "Stage A output: create these at the live DNS provider (No-IP) before cutover so ACM can issue the certificate."
  value       = local.acm_validation
}

output "redirect" {
  value = local.redirect_enabled ? {
    certificate_arn = aws_acm_certificate.redirect[0].arn
    function_arn    = aws_cloudfront_function.redirect[0].arn
    distribution_id = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].id : null
    distribution_dn = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].domain_name : null
  } : null
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "query_log_groups" {
  value = { for k, g in aws_cloudwatch_log_group.query : k => g.name }
}

output "failover_test_name" {
  value = var.enable_failover_test ? local.failover_test_name : null
}
