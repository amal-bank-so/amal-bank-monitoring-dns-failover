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
