# Production settings for the ebanking stack (no secrets).
# Parity with the DigiCert DNS Made Easy configuration observed in the recording:
#   A @ 37.34.133.35 (TTL 1800); failover location 2 91.140.155.171; monitor TCP 443.
ebanking_primary_ip   = "37.34.133.35"
ebanking_secondary_ip = "62.215.250.99"

# Carrier names, for labels and alarm descriptions only.
ebanking_primary_name   = "Zain"
ebanking_secondary_name = "FastTelco"

# Lowered from the observed 1800 (kept for parity until 2026-10-03) to 60 at the owner's request, so that a failover or
# failback reaches clients within about a minute (plus the health-check detection time of about 90 s).
ebanking_ttl = 60

# Provisional (the DigiCert "Medium" sensitivity does not map directly to Route 53 settings):
# a check every 30 s, three consecutive failures (about 90 s) to mark an endpoint unhealthy.
health_check_port              = 443
health_check_interval          = 30
health_check_failure_threshold = 3

# Failover to the secondary (FastTelco 62.215.250.99) is gated on its own Route 53 health check, as in the
# original design: Route 53 serves the secondary only while it is healthy. This was false from 2026-10-01 to
# 2026-10-03 because the previous secondary (91.140.155.171) blocked Route 53's health checkers; the FortiGate
# 62.215.250.99 is reachable from all 16 checker locations. Set to false to fail over without the check.
secondary_failover_requires_health_check = true
