# New public hosted zones. Creating a zone does not affect resolution: nothing
# resolves through it until the registrar / parent delegation is changed.

resource "aws_route53_zone" "amalbank_so" {
  name    = local.amalbank_zone
  comment = "Amal Bank: migrated from No-IP (delegation unchanged until cutover)"

  tags = local.tags_zone.amalbank_so
}

resource "aws_route53_zone" "ebanking" {
  name    = local.ebanking_zone
  comment = "Amal Bank e-banking failover zone, migrated from DigiCert DNS Made Easy (child delegation in amalbankso.com unchanged until cutover)"

  tags = local.tags_zone.ebanking
}
