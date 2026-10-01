# Amal Bank DNS migration: infrastructure code

Terraform for the AWS side of the migration described in
[`docs/MIGRATION_PLAN.md`](../docs/MIGRATION_PLAN.md).

**Status (2026-10-01):** applied to account `029288159395`: `bootstrap` (state bucket), `shared` (alert topic, no
subscribers) and `amalbank-so`, which is **fully live**: `amalbank.so` is delegated to Route 53, the certificate is issued,
and the apex and `www` are served by CloudFront (301 to the destination site). `ebanking` is not applied. The step-by-step for delegating and testing is
[`docs/RUNBOOK_amalbank_so.md`](../docs/RUNBOOK_amalbank_so.md).

```
infra/
  bootstrap/            one-time S3 state bucket (applied)
  stacks/
    shared/             alert topic + email subscriptions
    amalbank-so/        amalbank.so zone, record inventory, CloudFront redirect, query log
    ebanking/           ebanking.amalbankso.com zone, failover records, health checks, alarms, query log, failover test pair
  tools/                BIND converter, failover test driver, Function unit tests, credential helper, draft IAM policy
```

## Why three stacks

Each stack is its own root module with its **own state** (`<stack>/terraform.tfstate` in the state bucket), so
migrating `amalbank.so` (lower risk) can never plan, change or destroy anything belonging to the ebanking
failover, and the reverse. No stack reads another's state; the alert topic is found by name. Per-stack query
log resource policies keep them independent. `tools/test_stack_layout.py` enforces the separation.

**Apply order:** `shared` first, then `amalbank-so`, then `ebanking` (later, after `amalbank.so` is accepted).

| Stack | Default plan | Notes |
|---|---|---|
| `shared` | topic, topic policy, one subscription per `alert_emails` entry | Subscriptions need email confirmation. |
| `amalbank-so` | hosted zone, query log group + policy + config (5 resources) | Records, legacy web records, redirect and alarm are driven by the inventory/variables (below). |
| `ebanking` | zone, 2 health checks, 2 failover records, alarms, query log | Not used until after `amalbank.so`. `enable_failover_test` adds the simulation pair. |

## amalbank.so stack: direct delegation at the registrar

The registrar is pointed straight at the Route 53 zone, bypassing No-IP and DNS Made Easy, so nothing needs to
be added at the old providers. The old zone stays untouched as the rollback target. Sequence:

1. **Parity zone** (applied, no delegation yet): hosted zone, query logging, the record inventory, the apex/www
   `A` records pointing at the legacy redirect IP (`legacy_web_ips`, TTL 60), the apex NS TTL lowered to 900 s,
   an HTTP health check, and alarms (web health, NXDOMAIN answers) to the shared topic. The inventory is converted
   with `tools/bind_to_inventory.py --exclude @:A --exclude www:A` (apex/www are managed in `web.tf`, not the
   inventory). A verified inventory with no web address refuses to plan.
2. **`enable_certificate`** (applied): ACM certificate and its validation CNAMEs in this zone. ACM validates once the
   zone is delegated and abandons a request still pending after 72 hours.
3. **Delegate at the registrar** to exactly the four name servers of this zone. Visitors see no change because the
   apex/www still point at the same IP.
4. **`enable_redirect`** (applied): the CloudFront Function (needs `redirect_status_code`, `redirect_preserve_path`,
   `redirect_preserve_query`, which have no defaults).
5. **`enable_redirect_distribution`** (applied): the distribution with the Function attached, on its own
   `cloudfront.net` name only, so it is deployed before delegation.
6. **`enable_redirect_aliases`** (after delegation; needs the certificate ISSUED): adds `amalbank.so` / `www.amalbank.so`
   and the ACM certificate to the distribution.
7. **`web_use_cloudfront`**: `web.tf` switches the apex/www records in place from the legacy `A` records to CloudFront
   aliases (A + AAAA). The plan must show `~ update in-place`; **if it shows a replace, stop.**

Rollback of step 7 is `web_use_cloudfront = false` (records revert to the legacy IP, TTL 60); rollback of step 3 is
restoring the original registrar nameservers (bounded by delegation cache lifetimes). Production settings live in
`stacks/amalbank-so/terraform.tfvars`.

By default the redirect covers the apex and `www`, mirroring live behaviour (no wildcard exists today).
`redirect_wildcard=true` switches to apex + `*.amalbank.so`, which changes behaviour (unknown names stop
returning NXDOMAIN) and is one label deep only.

## Safety rails

- `allowed_account_ids` pins every stack to the expected account; `aws_region` must be `us-east-1`.
- The inventory is only applied when `"verified": true` and `source`/`exported_at` are recorded. Names must be
  relative (no zone suffix, no trailing dot), apex NS/SOA are rejected, and A/AAAA records that the redirect
  aliases will own fail the plan.
- The failover test never touches live endpoints.

## Usage

```bash
. infra/tools/aws-env.sh        # maps AWS_Access_key / AWS_Secret_Access_key (prints no secrets)
cd infra/stacks/shared
cp backend.hcl.example backend.hcl && terraform init -backend-config=backend.hcl
terraform plan -out=tfplan      # review, get approval, then apply the saved plan
```

Repeat per stack (each has its own `backend.hcl.example`). Offline checks, no credentials needed:
`terraform init -backend=false && terraform validate` in each stack; `terraform test` in `stacks/amalbank-so`; `node --test infra/tools/redirect.test.mjs`;
`python -m unittest discover -s infra/tools`. CI runs all of them.

## Tools (`infra/tools/`)

Install once: `pip install -r infra/tools/requirements.txt`.

### `bind_to_inventory.py`: BIND zone file to inventory JSON

```bash
python infra/tools/bind_to_inventory.py --zone amalbank.so --input amalbank.so.zone \
  --source "No-IP export <date>" --exported-at 2026-10-02T10:00:00Z \
  --exclude @:A --exclude www:A \
  --output infra/stacks/amalbank-so/inventory/amalbank.so.json --report /path/to/reconciliation-report.json
```

Relative names, `@` and wildcards are normalised; every value target becomes an absolute FQDN, so a zone
suffix can never be appended twice. It **fails** (and writes no inventory) on: owner names ending with the zone
name (missing trailing dot), out-of-zone records, a CNAME at the apex or beside other data, differing TTLs
inside one record set, unsupported record types (provider-specific types such as redirects must be handled
explicitly; `--allow-unsupported` still lists them), and unparseable input. Targets that look like a missing
trailing dot are warnings (`--strict` makes them errors). Apex NS/SOA are not imported but are listed in the
report so the original delegation can be saved. `--exclude NAME:TYPE` drops a record set as an intentional,
recorded exception (an exclusion that matches nothing is flagged). Output is always `"verified": false`; set it
to `true` by hand only after reconciling the report against the authoritative export.

### `failover_test.py`: isolated failover/failback/both-down test

Needs the **ebanking** stack applied with `enable_failover_test=true`. `. infra/tools/aws-env.sh && python
infra/tools/failover_test.py` (`--dry-run` lists the scenarios without AWS). It drives simulated health via
CloudWatch metrics, asks Route 53 what it would answer (`route53:TestDNSAnswer`; no delegation needed),
measures how long each change takes, and writes JSON + Markdown evidence to `infra/evidence/`. It refuses to
run unless the targets are the TEST-NET test pair with CLOUDWATCH_METRIC health checks and action-free alarms,
and always restores healthy state. It never touches the live ebanking records, health checks or alarms. It
does **not** test alert delivery (the test alarms deliberately notify nobody); that is a separate,
owner-approved check.

### `verify_zone.py`: zone verification

`verify_zone.py pre` compares the zone with the inventory using Route 53's own authoritative answers
(`TestDNSAnswer`; no delegation needed): every record, TTLs, the four apex name servers, no unexpected records,
and NXDOMAIN for unknown names. `verify_zone.py post [--wait SECONDS] [--web cloudfront]` adds public-resolver
checks (Google, Cloudflare, Quad9: is the delegation visible, do answers match) and certificate / distribution
state. Results are PASS, FAIL, PENDING (e.g. a resolver still caching the old delegation) or INFO; evidence is
written to `infra/evidence/`.

### `check_web.sh`: website checks from your network

Run where the domain is reachable. It does not follow redirects and prints status, `Location`, cache headers and
TLS details for the apex, www, a path-and-query URL, HEAD and POST. Run it before delegation to capture the legacy
behaviour, with `--cloudfront <domain>` to test the new distribution before switching DNS, and after the switch.

## Known limitations / to verify in Phase 2

- Query-string values in CloudFront Functions are re-serialised as received; test percent-encoded and
  empty-value parameters against the deployed Function (`aws cloudfront test-function`).
- A wildcard certificate/alias covers one label only.
- `tools/deployer-policy.json` is an untested draft; the current IAM user has AdministratorAccess.
- Health-check interval/threshold (30 s / 3) are provisional; `ebanking_ttl` starts at the observed 1800.
