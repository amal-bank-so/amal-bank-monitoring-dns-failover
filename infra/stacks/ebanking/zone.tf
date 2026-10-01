# New public hosted zone. Creating a zone does not affect resolution: nothing
# resolves through it until the child NS records in the amalbankso.com parent zone
# are changed (the registrar delegation of amalbankso.com is NOT touched).

resource "aws_route53_zone" "ebanking" {
  name    = local.ebanking_zone
  comment = "Amal Bank e-banking failover zone, migrated from DigiCert DNS Made Easy (child delegation in amalbankso.com unchanged until cutover)"

  tags = {
    Name = local.ebanking_zone
  }
}

# Route 53 generates the apex NS and SOA records and defaults the NS TTL to 172800
# (2 days). Keep the generated name servers but lower the TTL so that resolvers which
# cached this delegation return to the previous one within minutes if a rollback is
# needed. The parent (amalbankso.com) NS records for this name are a separate TTL.
resource "aws_route53_record" "apex_ns" {
  zone_id         = aws_route53_zone.ebanking.zone_id
  name            = local.ebanking_zone
  type            = "NS"
  ttl             = var.apex_ns_ttl
  records         = aws_route53_zone.ebanking.name_servers
  allow_overwrite = true
}
