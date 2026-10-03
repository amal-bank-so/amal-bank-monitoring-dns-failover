# Runbook: ebanking.amalbankso.com migration to Route 53

State as of 2026-10-01. Everything below is **applied in AWS account `029288159395`, us-east-1**, and **nothing is
delegated yet**: `ebanking.amalbankso.com` is still served by DigiCert DNS Made Easy. Delegating (section 3) is the only
step that changes what customers' resolvers use.

## 1. What is deployed (stack `infra/stacks/ebanking`)

| Item | Value |
|---|---|
| Hosted zone | `ebanking.amalbankso.com`, ID `Z01112481OCSOFIT54YT1` |
| **Name servers (give the parent zone exactly these four)** | `ns-1273.awsdns-31.org`, `ns-1926.awsdns-48.co.uk`, `ns-379.awsdns-47.com`, `ns-827.awsdns-39.net` |
| Records (parity with DigiCert) | `@ A 37.34.133.35` PRIMARY (**Zain**; set `ebanking-primary`, health check), `@ A 62.215.250.99` SECONDARY (**FastTelco**; set `ebanking-secondary`, health check attached since 2026-10-03, FortiGate; was `91.140.155.171` until 2026-10-03), both TTL 60 (was 1800 until 2026-10-03) |
| Apex NS TTL | 900 s (Route 53 default is 172800) |
| Health checks | primary `f40f28f3-46f7-4565-86b1-a9fa30f4c302` (TCP 443 on `37.34.133.35`), secondary `0ce5aea4-0529-40f4-bc6e-9a123b89fb61` (TCP 443 on `62.215.250.99`); every 30 s, three failures to mark unhealthy (about 90 s; provisional, DigiCert's "Medium" does not map directly) |
| Alarms | `amal-dns-ebanking-primary-unhealthy`, `amal-dns-ebanking-secondary-unhealthy`, `amal-dns-ebanking-both-unhealthy`; topic `amal-dns-alerts` has no subscribers by decision, so they notify nobody |
| Query logging | `/aws/route53/ebanking.amalbankso.com`, retention 365 days |

The live zone contains only the apex `A` (confirmed from the recording's zone definition and public probes: no other names,
no wildcard, no DNSSEC at the zone or the parent). Failover behaviour equals DigiCert's as understood: primary preferred,
secondary when the primary is unhealthy, automatic return when it recovers (the recording shows "turn off auto-failover after
first failure" unchecked).

## 2. Finding that changed the design: the original secondary could not be health-checked by Route 53 (resolved 2026-10-03)

**Update 2026-10-03:** the owner replaced the secondary with the FortiGate `62.215.250.99` (applied to production, 2 in-place changes). Route 53's checkers reach it from **16 of 16** locations, so the original blocker no longer applies. **Since 2026-10-03 the secondary record is gated** on its own health check (`secondary_failover_requires_health_check = true`): Route 53 serves the secondary only while it is healthy. The text below is the history of the original `91.140.155.171` endpoint. **DigiCert's failover location 2 must also be set to `62.215.250.99` by the owner** while resolvers are split between providers.

Measured after deployment from Route 53's 16 health-check locations:
- primary `37.34.133.35:443`: **16 of 16 connect**.
- secondary `91.140.155.171:443`: **16 of 16 time out**, although a control connection from another network reaches it. Its
  firewall allows other sources but not Route 53's health checkers.

If the secondary record required a healthy secondary check, a primary failure would leave both endpoints "unhealthy" and
Route 53 would keep answering with the primary: **failover would never happen**. So the secondary record has **no health
check attached** (`secondary_failover_requires_health_check = false`): when the primary is unhealthy Route 53 always answers
with the secondary, as DigiCert does today. The secondary check still exists for monitoring and its alarm stays in ALARM
until the secondary's firewall allows Route 53. To adopt the design in the migration brief (both gated), have the network
team allow the ranges of service `ROUTE53_HEALTHCHECKS` in <https://ip-ranges.amazonaws.com/ip-ranges.json> (31 prefixes at
the time of writing; the list changes) to TCP 443 on `91.140.155.171`, wait for the secondary alarm to return to OK, then set
`secondary_failover_requires_health_check = true` in `infra/stacks/ebanking/terraform.tfvars` and apply. No banking firewall was
changed by this migration.

TCP 443 only proves the port accepts connections, not that the application is healthy.

## 3. Delegate (owner step)

The delegation of `ebanking` lives in the **`amalbankso.com` zone at GoDaddy** (`ns53/ns54.domaincontrol.com`). Do **not**
change the registrar nameservers of `amalbankso.com`, and do not touch any other record there.
1. In the GoDaddy DNS for `amalbankso.com`, find the `NS` records for host **`ebanking`** (currently six DigiCert servers).
   Replace them with exactly the four name servers in section 1. Set their TTL to the lowest GoDaddy allows.
2. **Keep the DigiCert zone active and unchanged** (including its failover settings) for the whole observation period.
   Original delegation to restore on rollback: `ns20.digicertdns.com`, `ns21.digicertdns.com`, `ns22.digicertdns.com`,
   `ns23.digicertdns.net`, `ns24.digicertdns.net`, `ns25.digicertdns.net`.
3. Both providers answer identically, so nothing changes for customers while resolvers move over. Resolvers that cached the
   DigiCert delegation keep using it for up to its TTL (21600 s) plus the parent record's TTL.
4. Tell me when it is done. I check from Route 53's side (query log and health checkers) that the delegation is live.

## 4. Operating notes and differences from DigiCert

- **TTL is 60 s** since 2026-10-03 (owner request; it was 1800 for parity). After a failover or failback clients move within about a
  minute plus the health-check detection time (about 90 s: 30 s interval, three failures). Resolvers still on DigiCert keep DigiCert's
  1800 until they move to the AWS zone. The higher query volume costs cents. To change it, edit `ebanking_ttl` in `terraform.tfvars`.
- **Both endpoints failing:** Route 53 answers with the primary, so the name never becomes empty.
- **Failback is automatic** once the primary's check is healthy again.
- Failover has **not** been exercised against live endpoints (by design: a live banking endpoint is never disabled to
  demonstrate failure). The isolated test pair exists in the stack (`enable_failover_test`) and is off.

## 5. Rollback

At GoDaddy restore the six DigiCert `NS` records for `ebanking` (section 3). The AWS zone stays in place and keeps answering
resolvers that cached it (NS TTL 900 s at the zone, plus the parent's TTL). Fix the AWS zone as well if it was the cause.

## 6. Observation

At least 7 days and longer than the longest delegation cache lifetime. Watch the alarms in the CloudWatch console (they notify
nobody), review `/aws/route53/ebanking.amalbankso.com`, and keep DigiCert active and unchanged. Do not cancel DigiCert before the
observation period ends and you approve.

## Status log

- 2026-10-01 ~22:25Z: owner reported the `ebanking` NS records at GoDaddy replaced with the four AWS name servers.
  **Delegation is live as seen from AWS**: a temporary probe record that exists only in the AWS zone was resolved by 13 of 16
  Route 53 health-check locations (record and probe check removed afterwards); real queries for `ebanking.amalbankso.com A`
  began reaching the zone at 22:03Z (query log). Route 53 answers `37.34.133.35` (primary healthy: 16/16 locations connect).
  Secondary check still times out from all 16 (firewall), alarm `secondary-unhealthy` in ALARM as documented; `both-unhealthy`
  OK. Resolvers that cached the DigiCert delegation move over as their caches expire (up to 21600 s plus the parent TTL); both
  providers answer identically meanwhile. Observation period: at least 7 days (earliest 2026-10-08); keep DigiCert unchanged.

- 2026-10-01 23:26Z: re-check. Route 53 vantage points on the AWS zone: 11 of 16; 609 queries answered in 2 h from many resolvers, all NOERROR except 54 NXDOMAIN, which are the temporary probe names plus one `www.ebanking` lookup (the old zone had no `www` either). Primary healthy from 15/15 locations; secondary still blocked by its firewall (alarm `secondary-unhealthy` in ALARM as documented). Route 53 answers 37.34.133.35. Terraform drift none.

- 2026-10-02 03:48Z: re-check. 2603 queries answered by the zone since go-live (last hour: 439 from 358 resolvers), 2539 NOERROR and 64 NXDOMAIN, of which 12 are non-probe (`www.ebanking` A/AAAA/CAA and `_dmarc.ebanking`, none of which existed at DigiCert either). Route 53 vantage points on the AWS zone: 11 of 16 (unchanged, plateaued for the same reason as amalbank.so). Primary healthy 16/16, secondary still blocked by its firewall (alarm in ALARM as documented), Route 53 answers 37.34.133.35, no Terraform drift.

- 2026-10-02 04:20Z: scheduled hourly re-check. 3033 queries since go-live; last hour 640 queries from 537 resolvers (608 NOERROR, 32 NXDOMAIN, all `www.ebanking`/`_dmarc` style lookups that never existed at DigiCert). Primary 16/16 healthy, secondary still blocked by its firewall (alarm `secondary-unhealthy` in ALARM as documented), Route 53 answers 37.34.133.35, no Terraform drift.

- 2026-10-02 05:06Z: re-check. 3343 queries since go-live; last hour 437 from 367 resolvers (434 NOERROR, 3 NXDOMAIN, all `www.ebanking`, which never existed at DigiCert). Primary 16/16 healthy, secondary still blocked by its firewall (alarm in ALARM as documented), Route 53 answers 37.34.133.35, no Terraform drift.

- 2026-10-03: secondary endpoint changed from `91.140.155.171` (dead from Route 53's view) to the FortiGate `62.215.250.99` at the owner's request and approval. Plan reviewed (exactly two in-place updates), applied, then Route 53 health checkers: primary 16/16 connected, **secondary 16/16 connected**. Route 53 still answers `37.34.133.35` (primary healthy). The secondary alarm cleared to OK at 17:23Z once the metric recovered (healthy from 17:22Z); all ebanking alarms are OK. DigiCert not changed by this migration; owner to update its failover location 2.

- 2026-10-03: owner named the endpoints: primary **Zain** (`37.34.133.35`), secondary **FastTelco** (`62.215.250.99`). Applied as metadata only (5 in-place changes): health-check tags `Name`/`Carrier` (`ebanking-primary-Zain-...`, `ebanking-secondary-FastTelco-...`) and the three alarm descriptions. DNS records, set identifiers, TTLs and health-check settings untouched; both endpoints healthy 16/16; all ebanking alarms OK; no Terraform drift.

- 2026-10-03: failover to the secondary is now **gated on its health check** at the owner's request (`secondary_failover_requires_health_check = true`; one in-place update of the secondary record, attaching the existing health check). After applying: both Zain and FastTelco healthy from 16/16 locations, Route 53 answers `37.34.133.35`, all three ebanking alarms OK, records/TTLs unchanged, no Terraform drift. Behaviour now: primary while healthy; secondary only if the primary is unhealthy and the secondary is healthy; primary if both are unhealthy.

- 2026-10-03: TTL of both failover records lowered from 1800 to **60** at the owner's request (two in-place updates, TTL only). Verified after applying: records show ttl=60 with health checks attached, Zain and FastTelco both healthy 16/16, Route 53 answers `37.34.133.35`, all three alarms OK, apex NS TTL unchanged at 900, no Terraform drift.

- 2026-10-03: **email notifications deployed** (SendGrid via Lambda `amal-dns-notify`, key read from Secrets Manager `SendGrid_API` in eu-west-1). Exactly three e-banking emails: HIGH Failover from Primary (Zain) to Secondary (FastTelco) (new composite alarm `amal-dns-ebanking-failover`: primary unhealthy AND secondary healthy), HIGH Primary (Zain) is Back (`primary-unhealthy` alarm -> OK), CRITICAL E-Banking is Down (`both-unhealthy` -> ALARM). Lambda self-test from inside AWS: SendGrid key valid with mail.send permission, sender configured. **No recipients configured yet, so nothing is sent**; add them in `stacks/shared/terraform.tfvars` (`notification_recipients`) or the secret's `SENDGRID_TO_EMAILS`. Logo pending (`lambda/logo.png`). All four ebanking alarms OK, no Terraform drift.

- 2026-10-03: notifier updated: recipients are read from the Secrets Manager secret `SENDGRID_TO_EMAILS` (eu-west-1; 1 recipient configured), and the email header logo is read from the private S3 bucket `amal-dns-notify-assets-029288159395` (object `logo.png`, **not uploaded yet**, so emails use the text header until it is). Lambda self-test: SendGrid key valid with mail.send, sender configured, recipients 1, logo_found false. No email has been sent. No Terraform drift.

## Completion status (against the migration brief)

| Requirement | Status |
|---|---|
| Zone and failover records match the previous provider | Done (single apex A; PRIMARY/SECONDARY, TTL 1800) |
| Delegation of `ebanking` in the `amalbankso.com` parent (GoDaddy) | Done, propagating |
| Primary endpoint health check | Working from 16/16 locations |
| Secondary endpoint health check | **Working and gating failover** since 2026-10-03 (FortiGate `62.215.250.99`, 16/16) |
| Failover exercised against the live endpoints | **Not done, by design.** Isolated test pair exists but is off |
| Failback and both-down behaviour demonstrated | **Not demonstrated** (documented Route 53 behaviour only) |
| Banking TLS and application-level checks (login/read) | **Pending, owner: you** (needs an approved test account; no transactions) |
| Alert delivery | **Built, recipients configured, self-tested; not yet proven end to end**: no real alarm has fired, and the logo is not uploaded yet |
| 7+ stable days of observation | **Pending** (earliest 2026-10-08) |
| Retire DigiCert | **Not before** the observation period ends and you approve |
