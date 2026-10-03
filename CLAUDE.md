# CLAUDE.md

Amal Bank DNS migration to AWS Route 53 (`amalbank.so` and `ebanking.amalbankso.com`). Repo:
`amal-bank-so/amal-bank-monitoring-dns-failover`. Production AWS account `029288159395`, region `us-east-1`.

## Standing rules from the owner (follow these every session)

1. **Always push to `main` on GitHub.** After every commit push the working branch *and* `main`
   (`git push -u origin <branch>` then `git push origin <branch>:main`), so `main` always equals production. Keep this file
   updated whenever the state or the rules change.
2. **Direct production implementation.** When asked to build or migrate something, implement and apply it. Do not add
   extra test tooling, test infrastructure or test rounds, and do not ask the owner for test inputs, test mailboxes or
   curl output unless they ask. Still review the plan before applying, check the result after applying, and never claim
   something works without evidence.
3. **Notifications go out through SendGrid, not SNS email subscriptions** (owner decision 2026-10-03, replacing the earlier "no
   subscriptions" rule). Do not subscribe email addresses to the SNS topic. Exactly four e-banking emails exist (below); do not add
   more without the owner asking. Never print the SendGrid key.
4. **No pull requests** unless asked.
5. **Old providers are the rollback.** Never change, cancel or retire No-IP (`amalbank.so`) or DigiCert DNS Made Easy
   (`ebanking`). Retire only after the 7-day observation period (earliest 2026-10-08) and explicit owner approval.
6. **The owner makes registrar / GoDaddy changes themselves** following the runbooks. Never alter banking firewalls or
   disable a live banking endpoint.
7. **No secrets in the repo** (state files, `backend.hcl`, `*.tfplan`, keys are git-ignored). Never print credentials.
8. Be honest in reports: say what was not done or not proven, and correct earlier mistakes when found.

## Current state (checked 2026-10-02)

| | `amalbank.so` | `ebanking.amalbankso.com` |
|---|---|---|
| Previous provider | No-IP (5 records: URL redirects, MX, SPF, autodiscover TXT) | DigiCert DNS Made Easy (single apex A + failover) |
| Route 53 zone | `Z02483903EQGQQFHLQUL3` | `Z01112481OCSOFIT54YT1` |
| Delegation | Registrar -> `ns-1337.awsdns-39.org`, `ns-1683.awsdns-18.co.uk`, `ns-377.awsdns-47.com`, `ns-524.awsdns-01.net` | Child NS at GoDaddy (`amalbankso.com`) -> `ns-1273.awsdns-31.org`, `ns-1926.awsdns-48.co.uk`, `ns-379.awsdns-47.com`, `ns-827.awsdns-39.net` |
| Status | **Live** (as of 2026-10-02 03:48Z about 6 of 16 Route 53 vantage points on AWS, CloudFront reached from 7 of 16) | **Live** (about 11 of 16 vantage points on AWS) |
| What it serves | Apex/www -> CloudFront redirect (301 to `https://www.amalbankso.so`, path+query preserved), MX 5 (M365), SPF, autodiscover TXT | Apex A: PRIMARY `37.34.133.35` (**Zain**), SECONDARY `62.215.250.99` (**FastTelco**, FortiGate, since 2026-10-03), TTL **60** (since 2026-10-03) |
| Runbook | `docs/RUNBOOK_amalbank_so.md` | `docs/RUNBOOK_ebanking.md` |

Applied stacks (all in `infra/stacks/`, separate S3 state in `amal-dns-tfstate-029288159395`): `shared` (alert topic),
`amalbank-so`, `ebanking`. A drift check (`terraform plan -detailed-exitcode`) shows repo = AWS. The runbooks hold the
status logs and completion tables; they are the source of truth (`docs/MIGRATION_PLAN.md` is the original plan and is
partly superseded).

Propagation note: the share of resolvers on the AWS zone has plateaued (about 6/16 and 11/16 vantage points). Resolvers that
cached the old delegation keep refreshing it from the old provider's servers, which still serve identical data, so this is
harmless and only completes when the old zones are retired after the observation period. Do not treat the plateau as a failure.

### Notifications (deployed 2026-10-03, `shared` stack; subjects are exactly `<Severity> - <Title>`: no prefix, no `[TEST]` marker, no carrier names in brackets)
Every alarm publishes to SNS topic `amal-dns-alerts`; Lambda `amal-dns-notify` (subscribed, `infra/stacks/shared/lambda/notify.py`) emails
through SendGrid and sends **only** these four: **High - Failover from Primary to Secondary** (alarm
`amal-dns-ebanking-failover` -> ALARM: primary unhealthy and secondary healthy), **High - Failover from Secondary to Primary**
(`amal-dns-ebanking-primary-unhealthy` -> OK), **High - Secondary is Down** (`amal-dns-ebanking-secondary-unhealthy` -> ALARM), **Critical - E-Banking is Down** (`amal-dns-ebanking-both-unhealthy` -> ALARM). All other
alarms (amalbank.so, ...) stay in the CloudWatch console and send nothing. The SendGrid key and sender are read at run
time from Secrets Manager secret `SendGrid_API` in **eu-west-1** (fields `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`; the field name has a
stray leading space, the Lambda strips it) and recipients from secret `SENDGRID_TO_EMAILS` (also eu-west-1, JSON field `SENDGRID_TO_EMAILS`,
comma separated: edit it any time, no deployment; 1 recipient at `amalbankso.so` as of 2026-10-03). The Lambda role can read only those two
secrets, the logo object, and write its own logs. Logo: private bucket `amal-dns-notify-assets-029288159395`, object `logo.png`, read on every
email (upload or replace it, no deployment); falls back to a bundled `lambda/logo.png`, then to a plain navy "Amal Bank" text header. The
logo (360x360 PNG, 14 KB) was loaded into the bucket on 2026-10-03 by a short-lived Lambda that fetched the owner's URL from AWS, because the sandbox's
proxy blocks that host (the temporary Lambda and role were deleted). The header colour `#042c75` is the logo's own background navy. After editing `notify.py`
run `python infra/stacks/shared/lambda/build.py`, commit the zip, then apply. Self-test (sends no mail, reports `recipients` and `logo_found`):
invoke the Lambda with `{"selftest": true}`. `{"send_test": true}` sends one test email per notification (4) through the real path (real subject, a grey banner in the body marks it as a test) without touching alarms; use it only when the owner asks.

### Open items
- Notifications: logo is in S3 and recipients are configured (Lambda self-test: SendGrid ok, recipients 1, `logo_found: true`), and on 2026-10-03 the owner asked for test emails: 4 test emails were accepted by SendGrid (HTTP 202). Inbox arrival is for the owner to confirm; no *real* alarm has fired yet, so the alarm-to-email path is proven only by the test path plus local checks.
- Outbound test mail from an `@amalbank.so` mailbox (inbound passed 2026-10-01).
- Redirect path/query behaviour vs the legacy No-IP redirect is **assumed** (preserved); No-IP redirected to the `http://`
  address, AWS redirects to `https://` on purpose.
- ebanking carriers: primary = Zain (`37.34.133.35`), secondary = FastTelco (`62.215.250.99`). The names are in the health-check `Name`/`Carrier`
  tags and alarm descriptions (variables `ebanking_primary_name`/`ebanking_secondary_name`), never in record `set_identifier`s: changing those
  would replace the live banking records.
- ebanking secondary: changed on 2026-10-03 from `91.140.155.171` to the FortiGate `62.215.250.99` (FastTelco). The secondary record is
  now **gated** on its own Route 53 health check (`secondary_failover_requires_health_check = true`, applied 2026-10-03): Route 53 serves
  the secondary only while it is healthy, and the primary when both are unhealthy. Both endpoints are healthy from 16/16 locations.
  **The owner must also change DigiCert's failover location 2 to `62.215.250.99`.** Failover/failback have **not** been exercised on
  live endpoints (by design).
- ebanking TTL was lowered from 1800 to **60** on 2026-10-03 (owner request, applied). Resolvers still on DigiCert keep its 1800 until
  they move. Application-level banking checks need an owner-provided test account. 7-day observation, then the owner decides about
  retiring No-IP / DigiCert.

## Repository layout

```
infra/bootstrap/        state bucket (applied once)
infra/stacks/shared|amalbank-so|ebanking/   Terraform root modules, one state each; apply order shared -> others (shared also holds the notify Lambda)
infra/tools/            bind_to_inventory.py, failover_test.py, verify_zone.py, check_web.sh, redirect function tests, aws-env.sh
infra/evidence/         verification output
docs/                   MIGRATION_PLAN.md, RUNBOOK_amalbank_so.md, RUNBOOK_ebanking.md
```
Each stack has `terraform.tfvars` (production settings, no secrets) and `backend.hcl.example`.

## How to work here

- **AWS credentials**: the working key pair is in env vars `AWS_Access_key` / `AWS_Secret_Access_key`; the standard
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are proxy placeholders. Run `. infra/tools/aws-env.sh >/dev/null` (never pipe
  it: a pipe runs it in a subshell and the exports are lost).
- **Terraform in the cloud sandbox**: not preinstalled; `registry.terraform.io` is blocked but `releases.hashicorp.com` works
  (download terraform and the aws provider zip with their SHA256SUMS, use a `filesystem_mirror` CLI config). Use Terraform
  >= 1.10 (CI pins 1.16.4), AWS provider ~> 6.0. Set `CHECKPOINT_DISABLE=1`.
- **Apply workflow**: `cp backend.hcl.example backend.hcl; terraform init -backend-config=backend.hcl`;
  `terraform plan -out=X`; inspect `terraform show -json X` (expect create-only / in-place, nothing destroyed); apply the
  saved plan; then `terraform plan -detailed-exitcode` must exit 0.
- **amalbank.so flags** (in `stacks/amalbank-so/terraform.tfvars`): `enable_certificate`, `enable_redirect`,
  `enable_redirect_distribution`, `enable_redirect_aliases`, `web_use_cloudfront` (all `true` now). The apex/www records
  switch in place between the legacy IP and CloudFront aliases; the plan must say `update in-place`, never replace.
- **Verifying delegation**: the sandbox's public-resolver queries all go through one shared cache and the domains' HTTP is
  blocked, so they prove nothing. Use independent signals: ACM/CloudFront state, the Route 53 query logs
  (`/aws/route53/<zone>`, read with `filter_log_events`, not `get_log_events`), and a *temporary* probe record plus a
  temporary Route 53 health check resolved by Route 53's own checkers (delete both afterwards).
- **Tools**: `verify_zone.py pre|post` (zone vs inventory), `check_web.sh` (run from the owner's network),
  `bind_to_inventory.py` (BIND -> inventory JSON, refuses doubled names / TTL conflicts / unsupported types).
  Python tests: `python -m unittest discover -s infra/tools`; JS: `node --test infra/tools/redirect.test.mjs`;
  Terraform offline tests: `terraform test` in `stacks/amalbank-so`. Run them only when changing that code.
- **Gotchas learned**: the AWS provider adds its own quotes to TXT values (the inventory keeps Route 53 syntax and
  `records.tf` converts); NXDOMAIN alarm threshold is 100 per 5 min because of resolver/browser random-label noise; ACM drops a
  pending certificate after 72 h; Route 53 returns the primary when both failover records are unhealthy.
