# Amal Bank DNS migration: infrastructure code

Terraform for the AWS side of the migration described in
[`docs/MIGRATION_PLAN.md`](../docs/MIGRATION_PLAN.md).

**Status:** only `infra/bootstrap` has been applied (S3 state bucket
`amal-dns-tfstate-029288159395`, 2026-10-01). The three stacks below have **not** been applied; their
plans were run read-only against account `029288159395`.

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
| `amalbank-so` | hosted zone, query log group + policy + config (5 resources) | Records, redirect and alarm are opt-in (below). |
| `ebanking` | zone, 2 health checks, 2 failover records, alarms, query log | Not used until after `amalbank.so`. `enable_failover_test` adds the simulation pair. |

## amalbank.so stack in stages

1. **Zone** (default): the new zone only. Nothing resolves through it until the registrar delegation changes.
2. **Records:** convert the verified export with `tools/bind_to_inventory.py` into
   `stacks/amalbank-so/inventory/amalbank.so.json`, review, set `"verified": true`. Records from the old redirect
   service (apex/www A) are dropped with `--exclude @:A --exclude www:A` and recorded as exceptions.
3. **Redirect stage A** (`enable_redirect=true` plus `redirect_status_code`, `redirect_preserve_path`,
   `redirect_preserve_query`, which have no defaults): ACM certificate, validation CNAMEs, CloudFront Function.
   Add the `acm_validation_records` output at the live DNS provider (No-IP) so the certificate can issue.
4. **Redirect stage B** (`enable_redirect_distribution=true`): waits for ISSUED, creates the distribution, Route 53
   aliases and a 5xx alarm to the shared topic.

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
`terraform init -backend=false && terraform validate` in each stack; `node --test infra/tools/redirect.test.mjs`;
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

## Known limitations / to verify in Phase 2

- Query-string values in CloudFront Functions are re-serialised as received; test percent-encoded and
  empty-value parameters against the deployed Function (`aws cloudfront test-function`).
- A wildcard certificate/alias covers one label only.
- `tools/deployer-policy.json` is an untested draft; the current IAM user has AdministratorAccess.
- Health-check interval/threshold (30 s / 3) are provisional; `ebanking_ttl` starts at the observed 1800.
