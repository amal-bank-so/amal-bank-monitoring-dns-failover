# Production settings for the ebanking stack (no secrets).
# Parity with the DigiCert DNS Made Easy configuration observed in the recording:
#   A @ 37.34.133.35 (TTL 1800); failover location 2 91.140.155.171; monitor TCP 443.
ebanking_primary_ip   = "37.34.133.35"
ebanking_secondary_ip = "91.140.155.171"

# Kept at the observed 1800 for parity. Lower to 300 (cutover preparation) or the validated
# final value (proposed 60) by editing this and applying; failover then reaches clients faster.
ebanking_ttl = 1800

# Provisional (the DigiCert "Medium" sensitivity does not map directly to Route 53 settings):
# a check every 30 s, three consecutive failures (about 90 s) to mark an endpoint unhealthy.
health_check_port              = 443
health_check_interval          = 30
health_check_failure_threshold = 3

# The secondary endpoint accepts TCP 443 connections but its firewall blocks Route 53's health
# checkers (measured 2026-10-01: all 16 locations time out; the primary connects from all 16).
# Gating the secondary on that check would make failover impossible, so the secondary record is
# served whenever the primary is unhealthy, as at the previous provider. The secondary check
# remains as monitoring. Set to true once the secondary's firewall allows the Route 53
# health-checker ranges (service ROUTE53_HEALTHCHECKS in https://ip-ranges.amazonaws.com/ip-ranges.json).
secondary_failover_requires_health_check = false
