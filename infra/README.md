# Amal Bank DNS migration: infrastructure code

Terraform for the AWS side of the migration described in
[`docs/MIGRATION_PLAN.md`](../docs/MIGRATION_PLAN.md). **Status: only `infra/bootstrap` has been applied** (S3 state bucket
`amal-dns-tfstate-029288159395`, 2026-10-01). The migration stack in `infra/terraform` has not
been applied; its plans were run read-only against account `029288159395`.

```
infra/
  bootstrap/   one-time S3 state bucket (apply once, after approval)
  terraform/   the migration stack
    inventory/amalbank.so.json   verified record inventory (empty until Phase 0)
    functions/redirect.js.tftpl  CloudFront viewer-request redirect Function
    tools/                       Function unit tests, credential helper, draft IAM policy
```

## What the stack manages

| File | Resources |
|---|---|
| `zones.tf` | Public hosted zones `amalbank.so` and `ebanking.amalbankso.com` (no delegation change) |
| `records.tf` | `amalbank.so` records from the inventory file, guarded (see below) |
| `ebanking.tf` | PRIMARY/SECONDARY failover A records (`37.34.133.35` / `91.140.155.171`) and one TCP 443 health check each |
| `redirect.tf` | ACM cert (us-east-1), CloudFront Function + distribution, apex/wildcard aliases, in two stages |
| `monitoring.tf` | SNS topic, per-endpoint health alarms, both-down composite alarm, email subscriptions |
| `logging.tf` | Route 53 query logging, CloudWatch log groups with explicit retention |
| `failover_test.tf` | Optional isolated failover simulation on TEST-NET addresses (off by default) |

## Tools (`infra/terraform/tools/`)

Install once: `pip install -r infra/terraform/tools/requirements.txt`. Tests: `python -m unittest discover -s infra/terraform/tools`.

### `bind_to_inventory.py`: BIND zone file to inventory JSON

```bash
python tools/bind_to_inventory.py --zone amalbank.so --input amalbank.so.zone \
  --source "No-IP export <date>" --exported-at 2026-10-02T10:00:00Z \
  --output inventory/amalbank.so.json --report /path/to/reconciliation-report.json
```

Relative names, `@` and wildcards are normalised; every value target becomes an absolute FQDN, so a zone
suffix can never be appended twice. It **fails** (and writes no inventory) on: owner names ending with the
zone name (missing trailing dot), out-of-zone records, a CNAME at the apex or beside other data, differing
TTLs inside one record set, unsupported record types (provider-specific types such as redirects must be
handled explicitly; `--allow-unsupported` still lists them), and unparseable input. Targets that look like a
missing trailing dot are warnings (`--strict` makes them errors). Apex NS/SOA are not imported but are
listed in the report so the original delegation can be saved. Output is always `"verified": false`; set it to
`true` by hand only after reconciling the report against the authoritative export.

### `failover_test.py`: isolated failover/failback/both-down test

Needs the stack applied with `enable_failover_test=true`. `. tools/aws-env.sh && python tools/failover_test.py`
(`--dry-run` lists the scenarios without AWS). It drives simulated health via CloudWatch metrics, asks Route 53
what it would answer (`route53:TestDNSAnswer`; no delegation needed), measures how long each change takes, and
writes JSON + Markdown evidence to `infra/terraform/evidence/`. It refuses to run unless the targets are the
TEST-NET test pair with CLOUDWATCH_METRIC health checks and action-free alarms, and always restores healthy
state. It never touches the live ebanking records, health checks or alarms. It does **not** test alert
delivery (the test alarms deliberately notify nobody); that is a separate, owner-approved check.

## Safety rails built in

- `allowed_account_ids` pins the stack to the expected account; `aws_region` must be `us-east-1`.
- The inventory is only applied when `inventory/amalbank.so.json` has `"verified": true` **and** records its
  `source` and `exported_at`. Names must be relative (no zone suffix, no trailing dot), apex NS/SOA are rejected,
  and a conflict with the redirect aliases fails the plan.
- The redirect has no default status/path/query behaviour; `enable_redirect` fails until they are set from the
  verified live behaviour.
- Redirect is two-stage because ACM DNS validation needs the validation CNAME to resolve publicly before the
  zone is delegated: Stage A creates the certificate and prints `acm_validation_records` (add them at No-IP);
  Stage B (`enable_redirect_distribution`) waits for ISSUED and creates the distribution and aliases.
- The failover test never touches live endpoints.

## Usage

```bash
. infra/terraform/tools/aws-env.sh          # maps AWS_Access_key / AWS_Secret_Access_key (prints no secrets)
cd infra/bootstrap && terraform init && terraform apply     # DONE 2026-10-01; bootstrap state backed up at s3://amal-dns-tfstate-029288159395/bootstrap/terraform.tfstate
cd ../terraform
cp backend.hcl.example backend.hcl
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                  # review, then get approval before apply
```

Offline checks (no credentials): `terraform init -backend=false && terraform validate` and
`node --test infra/terraform/tools/redirect.test.mjs`. CI runs both.

## Known limitations / to verify in Phase 2

- Wildcard cert/alias covers one label (`a.amalbank.so`); deeper names will not have valid TLS.
- Query-string values in CloudFront Functions are re-serialised as received; test percent-encoded and
  empty-value parameters against the deployed Function (`aws cloudfront test-function`).
- `tools/deployer-policy.json` is an untested draft; the current IAM user has AdministratorAccess.
- Health-check interval/threshold (30 s / 3) are provisional; the `ebanking_ttl` starts at the observed 1800.
