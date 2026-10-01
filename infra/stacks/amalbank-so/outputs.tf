output "amalbank_so_zone" {
  description = "Hosted zone ID and the exact four Route 53 name servers for the amalbank.so registrar delegation."
  value = {
    zone_id      = aws_route53_zone.amalbank_so.zone_id
    name_servers = sort(aws_route53_zone.amalbank_so.name_servers)
  }
}

output "acm_validation_records" {
  description = "Stage A output: the certificate validation CNAMEs. They are created in this zone automatically; nothing is added at the old provider. ACM issues once the zone is delegated to Route 53."
  value       = local.acm_validation
}

output "redirect" {
  value = local.cert_enabled || local.function_enabled ? {
    certificate_arn = local.cert_enabled ? aws_acm_certificate.redirect[0].arn : null
    function_arn    = local.function_enabled ? aws_cloudfront_function.redirect[0].arn : null
    names           = local.redirect_names
    distribution_id = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].id : null
    distribution_dn = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].domain_name : null
  } : null
}

output "web_records" {
  description = "How the apex/www records are currently served: legacy A records or CloudFront aliases."
  value       = { for k, v in local.web_records : k => v.alias ? "alias -> CloudFront" : "A -> ${join(", ", var.legacy_web_ips)} (TTL ${var.legacy_web_ttl})" }
}

output "query_log_group" {
  value = aws_cloudwatch_log_group.query.name
}

output "web_health_check_id" {
  value = var.enable_web_health_check ? aws_route53_health_check.web_http[0].id : null
}
