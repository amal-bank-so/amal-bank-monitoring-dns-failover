# Production settings for the amalbank.so stack (no secrets).
# See ../../../docs/MIGRATION_PLAN.md section 2b and ../../README.md for the sequence.

# Apex/www keep pointing at the old provider's redirect service until web_use_cloudfront.
legacy_web_ips = ["34.198.182.201"]

# Requested now so ACM validates as soon as the zone is delegated. ACM abandons a
# request that is still pending after 72 hours; if that happens, re-request with
#   terraform apply -replace='aws_acm_certificate.redirect[0]'
enable_certificate = true

# Measured 2026-10-01 from 16 Route 53 health-check locations: the legacy redirect answers
# http://amalbank.so/ and http://www.amalbank.so/ with 301 Moved Permanently, and refuses
# connections on port 443 (so https://amalbank.so does not work today).
redirect_status_code = 301
# redirect_preserve_path / redirect_preserve_query are still unknown (the response body does
# not reveal them): set from check_web.sh output taken before delegation.

# Not yet enabled (see README):
#   enable_redirect              needs the verified redirect behaviour (status, path, query)
#   enable_redirect_distribution needs the certificate ISSUED, i.e. the zone delegated
#   web_use_cloudfront           switches apex/www to CloudFront after it has been tested
