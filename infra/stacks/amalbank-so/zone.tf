# New public hosted zone. Creating a zone does not affect resolution: nothing
# resolves through it until the registrar delegation is changed.

resource "aws_route53_zone" "amalbank_so" {
  name    = local.amalbank_zone
  comment = "Amal Bank: migrated from No-IP (delegation unchanged until cutover)"

  tags = {
    Name = local.amalbank_zone
  }
}

# Route 53 generates the apex NS and SOA records and defaults the NS TTL to 172800
# (2 days). Keep the generated name servers but lower the TTL so that resolvers which
# cached this delegation return to the previous one within minutes if a rollback is
# needed. (The SOA keeps its generated values: its 900 s TTL already caps negative
# caching at 15 minutes.) The parent (.so registry) delegation TTL is separate.
resource "aws_route53_record" "apex_ns" {
  zone_id         = aws_route53_zone.amalbank_so.zone_id
  name            = local.amalbank_zone
  type            = "NS"
  ttl             = var.apex_ns_ttl
  records         = aws_route53_zone.amalbank_so.name_servers
  allow_overwrite = true
}
