# Production settings for the amalbank.so stack (no secrets).
# See ../../../docs/MIGRATION_PLAN.md section 2b and ../../README.md for the sequence.

# Apex/www keep pointing at the old provider's redirect service until web_use_cloudfront.
legacy_web_ips = ["34.198.182.201"]

# Requested now so ACM validates as soon as the zone is delegated. ACM abandons a
# request that is still pending after 72 hours; if that happens, re-request with
#   terraform apply -replace='aws_acm_certificate.redirect[0]'
enable_certificate = true

# Redirect, deployed to production on CloudFront's own address ahead of delegation.
# Status 301 was measured on the live legacy redirect (16 Route 53 health-check locations).
# Path and query are PRESERVED: the standard behaviour for a domain redirect, and an
# assumption, since what the legacy service does with them could not be observed. To change
# it, edit these two values and apply (the Function is updated in place).
enable_redirect         = true
redirect_status_code    = 301
redirect_preserve_path  = true
redirect_preserve_query = true

enable_redirect_distribution = true

# Delegation to Route 53 is live (2026-10-01). Attach the names and certificate (this waits for ACM to
# issue the certificate), then point apex/www at CloudFront with web_use_cloudfront (rollback: false).
enable_redirect_aliases = true
web_use_cloudfront        = true
