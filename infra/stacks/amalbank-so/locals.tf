locals {
  stack_name = "amalbank-so"

  # Exact name, per the migration brief. amalbank.so, amalbankso.com and
  # amalbankso.so are three different domains, so this is a constant, not an input.
  amalbank_zone = "amalbank.so"

  # CloudFront's fixed hosted zone ID for alias records.
  cloudfront_zone_id = "Z2FDTNDATAQYW2"
}
