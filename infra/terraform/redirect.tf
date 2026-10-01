# amalbank.so redirect: CloudFront + viewer-request Function (replaces the
# No-IP redirect). Route 53 records cannot redirect URLs.
#
# Two stages, because ACM DNS validation needs the validation CNAME to be
# publicly resolvable and the zone is not delegated to Route 53 until cutover:
#   Stage A (enable_redirect): certificate, validation records, Function.
#     -> add the output `acm_validation_records` at the live DNS provider (No-IP).
#   Stage B (enable_redirect_distribution): wait for ISSUED, then distribution
#     and Route 53 aliases for the apex and wildcard.
#
# Note: the wildcard covers ONE label (a.amalbank.so) and the certificate only
# covers apex + *.amalbank.so, so deeper names (a.b.amalbank.so) will fail TLS.
# Test and decide in Phase 2 whether any such names exist.

locals {
  redirect_enabled = var.enable_redirect
  redirect_dist    = var.enable_redirect && var.enable_redirect_distribution
}

resource "terraform_data" "redirect_guard" {
  count = var.enable_redirect ? 1 : 0

  lifecycle {
    precondition {
      condition = (
        var.redirect_status_code != null &&
        var.redirect_preserve_path != null &&
        var.redirect_preserve_query != null
      )
      error_message = "Set redirect_status_code, redirect_preserve_path and redirect_preserve_query from the verified live redirect behaviour (plan task 0.7). They have no default on purpose."
    }
  }
}

resource "aws_acm_certificate" "redirect" {
  count = local.redirect_enabled ? 1 : 0

  domain_name               = local.amalbank_zone
  subject_alternative_names = ["*.${local.amalbank_zone}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [terraform_data.redirect_guard]
}

# Apex and wildcard share one validation CNAME, so a single record covers both.
# Its name/value are only known after the certificate exists, so the for_each key
# is static; a precondition confirms at apply time that both names really do share
# the same record.
locals {
  acm_dvo = local.redirect_enabled ? aws_acm_certificate.redirect[0].domain_validation_options : []
  acm_validation = local.redirect_enabled ? {
    validation = {
      name  = one([for d in local.acm_dvo : d.resource_record_name if d.domain_name == local.amalbank_zone])
      type  = one([for d in local.acm_dvo : d.resource_record_type if d.domain_name == local.amalbank_zone])
      value = one([for d in local.acm_dvo : d.resource_record_value if d.domain_name == local.amalbank_zone])
    }
  } : {}
}

resource "aws_route53_record" "acm_validation" {
  for_each = local.redirect_enabled ? toset(["validation"]) : toset([])

  zone_id         = aws_route53_zone.amalbank_so.zone_id
  name            = local.acm_validation[each.key].name
  type            = local.acm_validation[each.key].type
  ttl             = 300
  records         = [local.acm_validation[each.key].value]
  allow_overwrite = false

  lifecycle {
    precondition {
      condition     = length(distinct([for d in local.acm_dvo : d.resource_record_name])) == 1
      error_message = "ACM returned different validation records for the apex and wildcard names; this stack assumes they share one CNAME. Add a record per name."
    }
  }
}

resource "aws_acm_certificate_validation" "redirect" {
  count = local.redirect_dist ? 1 : 0

  certificate_arn         = aws_acm_certificate.redirect[0].arn
  validation_record_fqdns = [for r in aws_route53_record.acm_validation : r.fqdn]

  timeouts {
    create = "30m"
  }
}

resource "aws_cloudfront_function" "redirect" {
  count = local.redirect_enabled ? 1 : 0

  name    = "${var.name_prefix}-amalbank-so-redirect"
  runtime = "cloudfront-js-2.0"
  comment = "Redirect amalbank.so and *.amalbank.so to ${var.redirect_target}"
  publish = true

  code = templatefile("${path.module}/functions/redirect.js.tftpl", {
    target         = var.redirect_target
    status_code    = var.redirect_status_code
    preserve_path  = var.redirect_preserve_path
    preserve_query = var.redirect_preserve_query
    max_age        = var.redirect_cache_max_age
  })

  depends_on = [terraform_data.redirect_guard]
}

data "aws_cloudfront_cache_policy" "disabled" {
  count = local.redirect_dist ? 1 : 0
  name  = "Managed-CachingDisabled"
}

resource "aws_cloudfront_distribution" "redirect" {
  count = local.redirect_dist ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = var.redirect_price_class
  aliases         = local.redirect_alias
  comment         = "amalbank.so redirect to ${var.redirect_target}"

  # Minimal valid origin. Requests are answered by the viewer-request Function and
  # never reach it; if the Function were ever detached, requests would fall back to
  # the destination site itself (read-only, no change to that site).
  origin {
    origin_id   = "redirect-fallback"
    domain_name = trimprefix(var.redirect_target, "https://")

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "redirect-fallback"
    viewer_protocol_policy = "allow-all" # HTTP is redirected straight to the target, no extra https hop
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.disabled[0].id
    compress               = false

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.redirect[0].arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.redirect[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "redirect_alias" {
  for_each = local.redirect_dist ? {
    "apex-A"        = { name = local.amalbank_zone, type = "A" }
    "apex-AAAA"     = { name = local.amalbank_zone, type = "AAAA" }
    "wildcard-A"    = { name = "*.${local.amalbank_zone}", type = "A" }
    "wildcard-AAAA" = { name = "*.${local.amalbank_zone}", type = "AAAA" }
  } : {}

  zone_id = aws_route53_zone.amalbank_so.zone_id
  name    = each.value.name
  type    = each.value.type

  alias {
    name                   = aws_cloudfront_distribution.redirect[0].domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}
