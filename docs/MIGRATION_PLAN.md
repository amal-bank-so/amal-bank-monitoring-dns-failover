# Amal Bank DNS migration to AWS: plan and task list (for approval)

Status: **DRAFT, awaiting approval. AWS access verified. No AWS or DNS changes have been made.**
Prepared: 2026-09-30. Scope follows the supplied migration prompt: DNS hosting, ebanking failover, website redirects and monitoring only. Banking endpoints, Microsoft 365, the destination site and domain registration are untouched.

## 1. AWS availability check (result: READY, read-only verified 2026-09-30)

| Check | Result |
|---|---|
| Credentials | The standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` variables hold 14-character proxy placeholders and are rejected by AWS. The working key pair is in `AWS_Access_key` / `AWS_Secret_Access_key` (mixed case). Tooling must read those, or map them, for the AWS SDK/Terraform. |
| Identity (STS) | Account `029288159395`, IAM user `claude-amal-bank-worker` (long-term access key). **Please confirm this is the intended account.** |
| Permissions | Attached: `AdministratorAccess`, `DatabaseAdministrator`, `NetworkAdministrator`. Policy simulation allows every action needed (Route 53, CloudFront + Functions, ACM, SNS, CloudWatch, Logs, IAM roles, S3). This is far broader than needed. Recommend a scoped role before cutover. |
| Existing resources | Route 53: one hosted zone, `amalbankapi.com` (6 records) and a registered domain `amalbankapi.com`. CloudFront: one distribution `E26WH3RBO9P510` (API Gateway origin in eu-west-1, no aliases). ACM (us-east-1): a certificate for `esahal-api.amalbankso.so`. CloudTrail: multi-region trail `management-events`. **These belong to other workloads and will not be touched.** |
| Duplicates | No hosted zones for `amalbank.so` or `ebanking.amalbankso.com`, no health checks, no SNS topics, no CloudWatch alarms, no log groups and no CloudFront Functions exist. Nothing to reuse and nothing to conflict with. |
| Terraform / OpenTofu | Not installed in the sandbox. The repo is empty, so there are no conventions to follow. Needs installation (provider download through the sandbox proxy still to be checked). |
| Pricing | Not yet verified against AWS docs (the docs tool went away with the MCP server). Estimate in section 6 is provisional. |

Only read-only API calls were made. Nothing was created or changed.

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

### Live `amalbank.so` snapshot (public resolvers, 2026-10-02)

| Name | Type | TTL | Value |
|---|---|---|---|
| `@` | A | 60 | `34.198.182.201` (likely the No-IP redirect service) |
| `www` | A | 60 | `34.198.182.201` |
| `@` | MX | 60 | `5 amalbank-so.mail.protection.outlook.com.` |
| `@` | TXT | 300 | `"v=spf1 include:spf.protection.outlook.com -all"` |
| `autodiscover` | **TXT** | 300 | `"autodiscover.outlook.com"` (a TXT, not the CNAME seen in the DigiCert view; Outlook autodiscover normally needs a CNAME, so this is probably non-functional today) |
| `@` | NS / SOA | 21600 / 1800 | `ns1-ns4.no-ip.com`; SOA serial 2025121001, negative TTL 1800 |

- **No wildcard is live:** random labels (`zzq-probe-91827.amalbank.so`, also two levels deep) return NXDOMAIN. The DigiCert redirect entries for `*` are therefore not current behaviour. Decision needed: mirror live (apex + `www` only) or add a wildcard.
- No DKIM (`selector1/2._domainkey`), DMARC, SRV, CAA or DS records found for the common names probed. The authoritative export must confirm, because public probing cannot enumerate a zone.
- Redirect status and path/query behaviour could not be tested from the sandbox (outbound HTTP to the domain is blocked); the owner must supply it (task 0.7).
- Record TTLs are already 60-300 s; the only long cache is the NS delegation (21600 s at the child, parent-side TTL still to be measured).

Delegation cache lifetimes to plan around: NS TTL 21600 (6 h) as seen at the child, parent-side delegation TTLs still to be measured (TLDs typically use 24-48 h), and the ebanking A record at 1800 s.

## 2b. Decision: delegate `amalbank.so` directly at the registrar (2026-10-02)

The owner has registrar access and chose to bypass No-IP (and DigiCert) for `amalbank.so`: build a parity zone in
Route 53, then point the registrar straight at it. Consequences:

- No ACM validation record is ever needed at No-IP; the certificate validates through the Route 53 zone once it is
  delegated. The redirect becomes a separate, quickly reversible step after delegation (apex/www A records switch
  in place from the legacy redirect IP to CloudFront aliases).
- The old No-IP zone is **not** changed or cancelled. It remains the rollback target and must stay active for the
  observation period.
- Skipping the No-IP export removes the check that the AWS zone is complete. Compensating controls: the
  Microsoft 365 admin center DNS list for mail completeness, the owner's confirmation of any other dependants
  of `amalbank.so`, and Route 53 query logging to catch NXDOMAIN for names that were expected to exist. Public
  probing cannot enumerate a zone, so anything not in those sources is at risk of being lost on cutover.
- Scope note: this covers `amalbank.so` only. `ebanking.amalbankso.com` is a child of `amalbankso.com` (hosted at
  GoDaddy); delegating that whole parent domain is a different, larger change (see section 3).

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
- [x] 0.1 Verify AWS identity and record account ID and region (done: account `029288159395`, awaiting your confirmation that it is correct).
- [ ] 0.2 Trace delegation for all three names from the parent servers and query each authoritative server directly: delegation TTLs, SOA, negative TTL, DS, child delegations.
- [ ] 0.3 *(converter ready: `infra/tools/bind_to_inventory.py`)* Export the complete live `amalbank.so` zone and redirect settings from No-IP. Export the DigiCert zones including failover and monitor config. Reconcile the two and document every difference.
- [ ] 0.4 Export the `amalbankso.com` parent zone from GoDaddy, noting every record at or below `ebanking`.
- [ ] 0.5 Discover hidden records: DKIM selectors, DMARC, SRV, CAA, verification TXT, certificate-validation CNAMEs, child delegations.
- [ ] 0.6 Save timestamped backups and the exact original delegations. Agree a change freeze or synchronized change log.
- [ ] 0.7 Capture the live redirect behavior: status code, path and query handling, HTTP vs HTTPS, TLS behavior, `www` and deeper hostnames.

**Gate 0:** a verified inventory exists. If any source is unavailable, I continue staging only and report the specific blocker. No record values will be guessed.

### Phase 1: build on AWS (no delegation changes)
- [x] 1.1 (skeleton) Terraform in `infra/`, **split into three independent stacks with separate state**: `shared` (alert topic), `amalbank-so`, `ebanking`; apply order shared, then amalbank-so, then ebanking (see `infra/README.md`). Terraform 1.16, AWS provider ~> 6.0, S3 state via `infra/bootstrap`. Validated, unit-tested and planned read-only; stacks **not applied**. State bucket `amal-dns-tfstate-029288159395` created 2026-10-01 (versioned, SSE-S3, public access blocked, TLS-only). Choice recorded: Terraform.
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
- [ ] 2.3 *(driver ready: `infra/tools/failover_test.py`; needs the ebanking stack applied with `enable_failover_test`)* Failover tests using isolated test names and simulated health state only. Live banking endpoints are not disabled. Cover primary preferred, primary down, secondary down, both down, and recovery, plus alert delivery.
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
Reply with approval of this plan and the answers to section 3, including confirmation of AWS account `029288159395`. Until then, only read-only discovery is possible.
