# New public hosted zone. Creating a zone does not affect resolution: nothing
# resolves through it until the registrar delegation is changed.

resource "aws_route53_zone" "amalbank_so" {
  name    = local.amalbank_zone
  comment = "Amal Bank: migrated from No-IP (delegation unchanged until cutover)"

  tags = {
    Name = local.amalbank_zone
  }
}
