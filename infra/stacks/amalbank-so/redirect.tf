# amalbank.so redirect: CloudFront + viewer-request Function (replaces the
# old provider's redirect). Route 53 records cannot redirect URLs.
#
# Direct-delegation sequence (the registrar points straight at this zone; the old
# provider is bypassed, so nothing is ever added there). Each step is its own flag:
#   0.             Zone + inventory + legacy web A records (web.tf) = parity zone.
#   enable_certificate            ACM certificate + validation CNAMEs (in this zone).
#                                 ACM validates once the zone is publicly delegated, and
#                                 abandons a request that is still pending after 72 hours.
#   enable_redirect               The Function. Needs the verified redirect behaviour.
#   enable_redirect_distribution  Waits for the certificate to be ISSUED, then creates the
#                                 distribution. It serves on its own *.cloudfront.net name,
#                                 so it can be tested with the real Host header while the
#                                 apex/www records still point at the legacy IP.
#   web_use_cloudfront            Switches the apex/www records (web.tf) from the legacy A
#                                 records to CloudFront aliases, in place.
#
# Names: by default the apex and www, mirroring live behaviour (no wildcard exists
# today). redirect_wildcard = true switches to the apex and *.amalbank.so instead.
# A wildcard covers ONE label (a.amalbank.so); deeper names would fail TLS.

locals {
  cert_enabled     = var.enable_certificate
  function_enabled = var.enable_redirect
  redirect_dist    = var.enable_redirect_distribution
  web_alias        = var.enable_redirect_distribution && var.web_use_cloudfront

  redirect_names  = var.redirect_wildcard ? [local.amalbank_zone, "*.${local.amalbank_zone}"] : [local.amalbank_zone, "www.${local.amalbank_zone}"]
  redirect_labels = var.redirect_wildcard ? ["@", "*"] : ["@", "www"]

  # A wildcard shares the apex's validation CNAME, so it needs no record of its own.
  acm_validated_names = [for n in local.redirect_names : n if !startswith(n, "*.")]
}

resource "terraform_data" "redirect_guard" {
  count = var.enable_redirect || var.enable_redirect_distribution || var.web_use_cloudfront ? 1 : 0

  lifecycle {
    precondition {
      condition = !var.enable_redirect || (
        var.redirect_status_code != null &&
        var.redirect_preserve_path != null &&
        var.redirect_preserve_query != null
      )
      error_message = "Set redirect_status_code, redirect_preserve_path and redirect_preserve_query from the verified live redirect behaviour (plan task 0.7). They have no default on purpose."
    }
    precondition {
      condition     = !var.enable_redirect_distribution || (var.enable_certificate && var.enable_redirect)
      error_message = "enable_redirect_distribution needs enable_certificate and enable_redirect."
    }
    precondition {
      condition     = !var.web_use_cloudfront || var.enable_redirect_distribution
      error_message = "web_use_cloudfront needs enable_redirect_distribution."
    }
  }
}

resource "aws_acm_certificate" "redirect" {
  count = local.cert_enabled ? 1 : 0

  domain_name               = local.amalbank_zone
  subject_alternative_names = [for n in local.redirect_names : n if n != local.amalbank_zone]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

}

# One validation CNAME per non-wildcard name. Names/values are only known after the
# certificate exists, so for_each keys come from the static name list.
locals {
  acm_dvo = local.cert_enabled ? aws_acm_certificate.redirect[0].domain_validation_options : []
  acm_validation = local.cert_enabled ? {
    for n in local.acm_validated_names : n => {
      name  = one([for d in local.acm_dvo : d.resource_record_name if d.domain_name == n])
      type  = one([for d in local.acm_dvo : d.resource_record_type if d.domain_name == n])
      value = one([for d in local.acm_dvo : d.resource_record_value if d.domain_name == n])
    }
  } : {}
}

resource "aws_route53_record" "acm_validation" {
  for_each = local.acm_validation

  zone_id         = aws_route53_zone.amalbank_so.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.value]
  allow_overwrite = false

  lifecycle {
    precondition {
      condition = !var.redirect_wildcard || length(distinct([
        for d in local.acm_dvo : d.resource_record_name if contains([local.amalbank_zone, "*.${local.amalbank_zone}"], d.domain_name)
      ])) == 1
      error_message = "ACM returned different validation records for the apex and wildcard names; this stack assumes they share one CNAME. Add a record for the wildcard."
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
  count = local.function_enabled ? 1 : 0

  name    = "${var.name_prefix}-amalbank-so-redirect"
  runtime = "cloudfront-js-2.0"
  comment = "Redirect ${join(", ", local.redirect_names)} to ${var.redirect_target}"
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
  aliases         = local.redirect_names
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

  depends_on = [terraform_data.redirect_guard]
}
