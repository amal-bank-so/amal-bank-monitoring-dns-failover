# Apex and www address records. One resource, two modes, so the switch from the
# legacy provider's redirect IP to CloudFront is a single in-place change to the
# same record (no delete-then-create gap):
#   before enable_redirect_distribution: A records -> var.legacy_web_ips (parity)
#   after:                               A + AAAA aliases -> CloudFront
# Records here are managed outside the inventory; the inventory must not define them.

locals {
  web_labels_legacy = ["@", "www"]

  web_records = local.redirect_dist ? {
    for pair in setproduct(local.redirect_labels, ["A", "AAAA"]) :
    "${pair[0]}-${pair[1]}" => { label = pair[0], type = pair[1], alias = true }
    } : length(var.legacy_web_ips) > 0 ? {
    for l in local.web_labels_legacy : "${l}-A" => { label = l, type = "A", alias = false }
  } : {}
}

# Delegating with a verified inventory but no web address would take the site down.
resource "terraform_data" "web_guard" {
  lifecycle {
    precondition {
      condition     = !local.inventory.verified || local.redirect_dist || length(var.legacy_web_ips) > 0 || var.no_web_records
      error_message = "The inventory is verified but the apex/www would have no address record. Set legacy_web_ips from the export (they are excluded from the inventory with bind_to_inventory.py --exclude), enable the redirect distribution, or acknowledge with no_web_records = true."
    }
  }
}

resource "aws_route53_record" "web" {
  for_each = local.web_records

  zone_id = aws_route53_zone.amalbank_so.zone_id
  name    = each.value.label == "@" ? local.amalbank_zone : "${each.value.label}.${local.amalbank_zone}"
  type    = each.value.type
  ttl     = each.value.alias ? null : var.legacy_web_ttl
  records = each.value.alias ? null : var.legacy_web_ips

  dynamic "alias" {
    for_each = each.value.alias ? [1] : []
    content {
      name                   = aws_cloudfront_distribution.redirect[0].domain_name
      zone_id                = local.cloudfront_zone_id
      evaluate_target_health = false
    }
  }

  depends_on = [terraform_data.web_guard]
}
