output "amalbank_so_zone" {
  description = "Hosted zone ID and the exact four Route 53 name servers for the amalbank.so registrar delegation."
  value = {
    zone_id      = aws_route53_zone.amalbank_so.zone_id
    name_servers = sort(aws_route53_zone.amalbank_so.name_servers)
  }
}

output "acm_validation_records" {
  description = "Stage A output: create these CNAMEs at the live DNS provider (No-IP) before cutover so ACM can issue the certificate."
  value       = local.acm_validation
}

output "redirect" {
  value = local.redirect_enabled ? {
    certificate_arn = aws_acm_certificate.redirect[0].arn
    function_arn    = aws_cloudfront_function.redirect[0].arn
    names           = local.redirect_names
    distribution_id = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].id : null
    distribution_dn = local.redirect_dist ? aws_cloudfront_distribution.redirect[0].domain_name : null
  } : null
}

output "query_log_group" {
  value = aws_cloudwatch_log_group.query.name
}
