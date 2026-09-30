# Amal Bank DNS migration to AWS: plan and task list (for approval)

Status: **DRAFT, awaiting approval. No AWS or DNS changes have been made.**
Prepared: 2026-09-30. Scope follows the supplied migration prompt: DNS hosting, ebanking failover, website redirects and monitoring only. Banking endpoints, Microsoft 365, the destination site and domain registration are untouched.

## 1. AWS availability check (result: NOT READY)

| Check | Result |
|---|---|
| AWS MCP server | Disconnected. It needs interactive re-authentication (`/mcp`, or the claude.ai connector settings), which a non-interactive cloud session cannot do. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the environment | Present, but STS `GetCallerIdentity` returns `InvalidClientTokenId`. The keys are invalid, expired or not for this account. |
| AWS account identity | **Not verified.** The prompt requires this before any resource is created, so nothing will be created. |
| Service availability | Route 53, CloudFront and ACM (certificate must be in us-east-1) are global or us-east-1 services, so availability is not expected to be a problem. Not confirmed through AWS APIs because of the above. |
| Terraform / OpenTofu | Not installed in the sandbox (`boto3` and `dnspython` installed in a scratch venv only). The repository is empty (no commits), so there are no existing conventions or resources to reuse. |
| Pricing | Not verified against AWS docs (AWS doc tools unavailable). Estimate in section 6 is provisional. |

**Needed from you:** re-authorize the AWS MCP connector, or provide a working credential set for the correct account. Give the expected AWS account ID so I can compare it with the identity returned. Confirm the role's permissions cover Route 53, CloudFront, ACM, SNS, CloudWatch, CloudWatch Logs, IAM (scoped) and CloudTrail (read).

## 2. Live DNS evidence (read-only, public recursive resolvers, 2026-09-30)

These were observed from public resolvers only. Direct queries to authoritative servers were not reliable from the sandbox, so the authoritative exports in Phase 0 remain mandatory.

| Finding | Observed | Why it matters |
|---|---|---|
| **amalbank.so is live on No-IP, not DigiCert** | NS are `ns1..ns4.no-ip.com` (TTL 21600). SOA is `ns2.no-ip.com`, serial 2025121001, negative TTL 1800. | The DigiCert zone shown in the recording appears to be **inactive and stale**. It must not be used as the source of truth. |
| Live vs DigiCert records differ | Live MX is `5 amalbank-so.mail.protection.outlook.com` with TTL 60. The recording shows MX `0` with TTL 3600. Live TXT TTL is 300, versus 3600 in the recording. | Importing the DigiCert view would change mail routing priority and TTLs. |
| Live `autodiscover.amalbank.so` | No CNAME returned. | The recording shows one in DigiCert only. Do not add it without confirming in the No-IP export. |
| Live web redirect | `amalbank.so` and `www.amalbank.so` resolve to `34.198.182.201` (TTL 60). This is most likely No-IP's redirect service. | The real redirect status and path/query behavior must be captured from No-IP and live `curl` tests, not assumed. |
| DNSSEC | No DS seen for `amalbank.so`, `amalbankso.com` or `ebanking.amalbankso.com`, and no AD flag. | Probably unsigned, so no DS transition is needed. **Must be confirmed at the parent servers** in Phase 0. |
| ebanking delegation | `ebanking.amalbankso.com` is a **child zone** delegated to 6 DigiCert name servers (`ns20-22.digicertdns.com`, `ns23-25.digicertdns.net`), NS TTL 21600. The A record is `37.34.133.35`, TTL 1800. | The cutover is a child NS change in the parent zone, not a registrar change. |
| Parent zone `amalbankso.com` | Served by **GoDaddy** (`ns53/ns54.domaincontrol.com`, NS TTL 3600, SOA negative TTL 60). It has MX and TXT (SPF, `MS=ms95441118`). | GoDaddy access is needed to change the ebanking NS records, and the other parent records must be preserved. |
| Separate destination site | `amalbankso.so` resolves to `167.235.241.124`. `www` is a CNAME to it. | This is the redirect target. It is not changed. |
| Names | `amalbank.so`, `amalbankso.com` and `amalbankso.so` are all distinct and in use. | Preserve exactly. |

Delegation cache lifetimes to plan around: NS TTL 21600 (6 h) as seen at the child, parent-side delegation TTLs still to be measured (TLDs typically use 24-48 h), and the ebanking A record at 1800 s.

## 3. Decisions needed from you

1. **Source of truth for `amalbank.so`:** provide No-IP access (or a full zone and redirect export) to confirm it is authoritative. Recommended.
2. **Registrar and parent access:** who controls the `.so` registrar account for `amalbank.so`, and the GoDaddy DNS for `amalbankso.com`?
3. **Change window:** any required bank change-control window and approver.
4. **Failback policy:** with health checks on both failover records, Route 53 returns to the primary as soon as it recovers and returns the primary when both are unhealthy. Is automatic failback acceptable? The recording shows "turn off auto-failover after first failure" unchecked, which suggests yes.
5. **Alert recipients:** named, owner-approved email addresses for SNS. "Account Owner" is not an address I will guess.
6. **Health-check reachability:** can the network team allow Route 53 health-checker source ranges to TCP 443 on `37.34.133.35` and `91.140.155.171`? I will not change firewalls myself.
7. **IaC tool:** recommend **Terraform**, since the repo is empty. Say so if the bank standard is something else, such as CDK or OpenTofu.
8. **Approved test accounts and test mailboxes** for TLS, login/read and mail tests, or accept these as owner-run acceptance checks.

## 4. Plan

Each phase has a gate. I stop at a failed gate and report.

### Phase 0: authoritative inventory (blocked on access)
- [ ] 0.1 Verify AWS identity and record account ID and region. **Blocked on AWS access.**
- [ ] 0.2 Trace delegation for all three names from the parent servers and query each authoritative server directly: delegation TTLs, SOA, negative TTL, DS, child delegations.
- [ ] 0.3 Export the complete live `amalbank.so` zone and redirect settings from No-IP. Export the DigiCert zones including failover and monitor config. Reconcile the two and document every difference.
- [ ] 0.4 Export the `amalbankso.com` parent zone from GoDaddy, noting every record at or below `ebanking`.
- [ ] 0.5 Discover hidden records: DKIM selectors, DMARC, SRV, CAA, verification TXT, certificate-validation CNAMEs, child delegations.
- [ ] 0.6 Save timestamped backups and the exact original delegations. Agree a change freeze or synchronized change log.
- [ ] 0.7 Capture the live redirect behavior: status code, path and query handling, HTTP vs HTTPS, TLS behavior, `www` and deeper hostnames.

**Gate 0:** a verified inventory exists. If any source is unavailable, I continue staging only and report the specific blocker. No record values will be guessed.

### Phase 1: build on AWS (no delegation changes)
- [ ] 1.1 Terraform project with remote state, backend, tags and a reviewed plan/diff. The choice is recorded.
- [ ] 1.2 Create public hosted zones `amalbank.so` and `ebanking.amalbankso.com`. Record zone IDs and the four name servers each.
- [ ] 1.3 Import verified records with BIND names normalized (no doubled zone names). Keep the AWS-generated NS/SOA. Preserve TTLs initially.
- [ ] 1.4 ebanking failover: PRIMARY `37.34.133.35` and SECONDARY `91.140.155.171` with distinct set identifiers. One fixed-endpoint TCP 443 health check each, starting at 30 s interval and threshold 3 (provisional).
- [ ] 1.5 CloudFront distribution with a viewer-request Function for redirects to `https://www.amalbankso.so`. Status and path/query handling come from Task 0.7. Covers apex and wildcard, with Route 53 aliases and an ACM certificate in us-east-1 (DNS-validated). Minimal valid origin, with the fallback path tested. Explicit names such as `autodiscover` and any MX-related hosts take precedence over the wildcard.
- [ ] 1.6 CloudWatch alarms on the health checks, SNS topic, and email subscriptions with confirmation.
- [ ] 1.7 Route 53 query logging to CloudWatch Logs with documented retention and scoped access. Scoped IAM roles and resource tags. No Traffic Flow and no Resolver endpoints.

**Gate 1:** `terraform plan` reviewed and approved by you before `apply`.

### Phase 2: test before any delegation change
- [ ] 2.1 Query all four AWS name servers for each zone. Compare every name/type/value with the authoritative export. Keep an explicit exception list. Confirm INSYNC.
- [ ] 2.2 Test redirects (HTTP, HTTPS, TLS, deeper hostnames, loops, precedence) and banking TLS for the real hostname against the candidate endpoint.
- [ ] 2.3 Failover tests using isolated test names and simulated health state only. Live banking endpoints are not disabled. Cover primary preferred, primary down, secondary down, both down, and recovery, plus alert delivery.
- [ ] 2.4 Mail DNS parity check (MX, SPF, DKIM, DMARC, autodiscover as verified).
- [ ] 2.5 Test the rollback procedure in staging.

**Gate 2:** all tests pass and the exceptions are approved.

### Phase 3: prepare and cut over (one zone at a time)
- [ ] 3.1 Write the rollback and runbooks with saved original values.
- [ ] 3.2 Lower record TTLs to 300 s and NS TTLs to 300-900 s where supported. Wait for the previous TTLs to expire, using measured cache lifetimes.
- [ ] 3.3 DNSSEC: confirm unsigned at the parents. If a DS is found, follow the DS-removal procedure first.
- [ ] 3.4 **Cutover 1: `amalbank.so`.** At its registrar, replace the No-IP delegation with exactly the four Route 53 name servers. Verify resolution, redirects, TLS and mail, then run test mail.
- [ ] 3.5 **Cutover 2: `ebanking.amalbankso.com`** (only after 3.4 passes). Update the child NS records in the GoDaddy `amalbankso.com` zone. The `.com` registrar delegation is **not** changed. Other parent records are preserved.
- [ ] 3.6 Monitor authoritative and independent recursive answers, health checks, TLS, redirects and mail. Keep the old providers fully operational and consistent.

**Gate 3:** any migration-caused SERVFAIL/NXDOMAIN, TLS failure, misrouting, redirect failure or mail interruption triggers an immediate rollback. Rollback restores the original delegation (subject to cache TTLs) and repairs AWS records or the Function.

### Phase 4: observe and close
- [ ] 4.1 Observe at least 7 stable days and longer than the longest measured delegation cache lifetime.
- [ ] 4.2 Raise the ebanking failover TTL to the validated final value (proposed 60 s).
- [ ] 4.3 Deliver: code and outputs, zone IDs and name-server sets, before/after record inventories, certificate and distribution IDs, health-check and alarm config, notification verification, test evidence, cost estimate, and cutover/rollback/operating runbooks. No secrets.
- [ ] 4.4 Decide on old-provider retirement (No-IP, DigiCert). **I will not cancel any subscription.** Retirement is earliest after Phase 4.1.

Completion is declared only if all required checks pass. Anything that cannot be performed is labelled pending with an owner.

## 5. Risks and notes
- **Wrong source of truth** is the biggest risk. The DigiCert `amalbank.so` zone appears inactive, so importing from the recording would change MX priority and TTLs.
- **Route 53 cannot do URL redirects.** The CloudFront Function replaces No-IP's redirect. The wildcard redirect must not shadow explicit records.
- **TCP 443 health checks prove reachability only,** not application readiness. An HTTPS/SNI check is added only if a suitable endpoint and expected response are agreed.
- **Health-check firewalling:** if Route 53 checkers are blocked, both endpoints will look down and the primary will be served. Network team action is required in advance.
- **Both-down behavior:** Route 53 returns the primary when both records are unhealthy, which may differ from DigiCert's current behavior (to be confirmed with the service owner).
- **Delegation rollback is bounded by caches:** resolvers that have already cached the AWS delegation keep using it until the TTL expires.

## 6. Provisional cost (unverified against AWS pricing; to be confirmed before approval)
| Item | Approx. monthly |
|---|---|
| 2 hosted zones (USD 0.50 each) | ~1.00 |
| 2 health checks (non-AWS endpoints, about USD 0.50-0.75 each) | ~1.00-1.50 |
| Standard queries (581k/month at about USD 0.40 per million) | ~0.25 |
| CloudFront (redirect requests), Function, ACM certificate | ~0 to a few cents; ACM public certificate is free |
| CloudWatch alarms, logs, SNS email | ~0.50-2 |
| **Total** | **about USD 3-6 per month** |

## 7. Approval requested
Reply with approval of this plan and the answers to section 3, plus restored AWS access. Until then, the only activity possible is read-only public DNS discovery.
