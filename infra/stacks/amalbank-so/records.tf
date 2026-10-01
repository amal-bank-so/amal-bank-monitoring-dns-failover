# Records for amalbank.so are driven by inventory/amalbank.so.json. Nothing is
# created until that file is marked verified, so an unreviewed or guessed
# inventory can never reach Route 53.

locals {
  inventory         = jsondecode(file("${path.module}/${var.inventory_file}"))
  inventory_records = local.inventory.verified ? local.inventory.records : []

  allowed_record_types = ["A", "AAAA", "CAA", "CNAME", "MX", "SRV", "TXT", "NS", "DS"]

  records = {
    for r in local.inventory_records :
    "${r.name}|${r.type}" => {
      fqdn = r.name == "@" ? local.amalbank_zone : "${r.name}.${local.amalbank_zone}"
      type = r.type
      ttl  = r.ttl
      # The inventory uses Route 53 syntax (TXT values include their double quotes). The
      # AWS provider adds the outer quotes itself and takes "" inside a value as the
      # boundary between 255-byte strings, so: strip the outer quotes and turn the
      # '" "' between strings into '""'. Other types pass through unchanged.
      values = contains(["TXT", "SPF"], r.type) ? [
        for v in r.values : replace(replace(v, "/^\"(.*)\"$/", "$1"), "\" \"", "\"\"")
      ] : r.values
    }
  }

  # Apex/www (and wildcard, if chosen) address records are owned by web.tf. The
  # inventory must list them as intentional exceptions (--exclude), not records.
  redirect_owned = flatten([for l in distinct(concat(local.web_labels_legacy, local.redirect_labels)) : ["${l}|A", "${l}|AAAA"]])
}

# Fails the plan on a malformed or conflicting inventory.
resource "terraform_data" "inventory_guard" {
  input = local.inventory.verified

  lifecycle {
    precondition {
      condition     = !local.inventory.verified || (local.inventory.source != null && local.inventory.exported_at != null)
      error_message = "A verified inventory must record its 'source' and 'exported_at'."
    }
    precondition {
      condition     = !local.inventory.verified || length(local.inventory.records) > 0
      error_message = "inventory/amalbank.so.json is marked verified but contains no records."
    }
    precondition {
      condition = alltrue([
        for r in local.inventory_records :
        !endswith(r.name, ".") && !endswith(r.name, local.amalbank_zone)
      ])
      error_message = "Record names must be relative to the zone: no trailing dot and no zone suffix (avoids doubled names such as www.amalbank.so.amalbank.so)."
    }
    precondition {
      condition     = alltrue([for r in local.inventory_records : contains(local.allowed_record_types, r.type)])
      error_message = "Unsupported record type in inventory (allowed: ${join(", ", local.allowed_record_types)})."
    }
    precondition {
      condition     = alltrue([for r in local.inventory_records : !(r.name == "@" && contains(["NS", "SOA"], r.type))])
      error_message = "Do not import apex NS/SOA; Route 53 generates them."
    }
    precondition {
      condition = alltrue([
        for r in local.inventory_records :
        alltrue([for v in r.values : startswith(v, "\"") && endswith(v, "\"") && length(v) >= 2])
        if contains(["TXT", "SPF"], r.type)
      ])
      error_message = "TXT/SPF inventory values must be in Route 53 syntax: each string in double quotes (e.g. \"v=spf1 -all\"). bind_to_inventory.py produces this."
    }
    precondition {
      condition     = alltrue([for r in local.inventory_records : r.ttl > 0 && length(r.values) > 0])
      error_message = "Every record needs a positive ttl and at least one value."
    }
    precondition {
      condition     = length(setintersection(keys(local.records), local.redirect_owned)) == 0
      error_message = "The inventory defines A/AAAA records for the apex, www or wildcard, which web.tf owns (legacy A records now, CloudFront aliases later). Exclude them from the inventory as recorded exceptions (bind_to_inventory.py --exclude) and put the legacy address in legacy_web_ips."
    }
  }
}

resource "aws_route53_record" "inventory" {
  for_each = local.records

  zone_id = aws_route53_zone.amalbank_so.zone_id
  name    = each.value.fqdn
  type    = each.value.type
  ttl     = each.value.ttl
  records = each.value.values

  allow_overwrite = false

  depends_on = [terraform_data.inventory_guard]
}
