locals {
  # Exact names, per the migration brief. amalbank.so, amalbankso.com and
  # amalbankso.so are three different domains, so they are constants, not inputs.
  amalbank_zone  = "amalbank.so"
  ebanking_zone  = "ebanking.amalbankso.com"
  redirect_alias = ["amalbank.so", "*.amalbank.so"]

  # CloudFront's fixed hosted zone ID for alias records.
  cloudfront_zone_id = "Z2FDTNDATAQYW2"

  tags_zone = {
    amalbank_so = { Name = local.amalbank_zone }
    ebanking    = { Name = local.ebanking_zone }
  }
}
