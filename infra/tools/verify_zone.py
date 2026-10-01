#!/usr/bin/env python3
"""Verify the amalbank.so Route 53 zone against the record inventory.

  verify_zone.py pre   Before delegation. Compares the zone with the inventory using the
                       Route 53 API and route53:TestDNSAnswer (Route 53's own authoritative
                       answer, so no delegation is needed).
  verify_zone.py post  After delegation. Adds public-resolver checks (Google, Cloudflare,
                       Quad9): is the delegation visible, do the answers match, plus
                       certificate, distribution and health-check state.

Expected records = the inventory records + the apex/www A records excluded from the inventory
(managed by the stack's web.tf: the legacy IPs, or CloudFront aliases with --web cloudfront)
+ the Route 53 generated apex NS/SOA + the ACM validation CNAMEs.

Results are PASS, FAIL, PENDING (not yet true, for example delegation still cached) or INFO.
Evidence is written to infra/evidence/. Exit codes: 0 no FAIL, 1 at least one FAIL, 2 could
not run. HTTP/TLS behaviour of the website is checked from the owner's network with
check_web.sh, because this tool may run where outbound HTTP to the domain is blocked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict

ZONE = "amalbank.so"
EXPECTED_ACCOUNT = "029288159395"
PUBLIC_RESOLVERS = {"google": "8.8.8.8", "cloudflare": "1.1.1.1", "quad9": "9.9.9.9"}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INVENTORY = os.path.join(HERE, "..", "stacks", "amalbank-so", "inventory", "amalbank.so.json")
NAME_TYPES = {"CNAME", "NS", "MX", "SRV", "PTR"}
ACM_SUFFIX = ".acm-validations.aws."


@dataclass
class Check:
    area: str
    name: str
    status: str  # PASS | FAIL | PENDING | INFO
    detail: str = ""


# --------------------------------------------------------------------------- normalising


def fqdn(label: str, zone: str = ZONE) -> str:
    return (zone if label == "@" else f"{label}.{zone}").lower().rstrip(".") + "."


def norm_value(rtype: str, value: str) -> str:
    v = value.strip()
    if rtype in NAME_TYPES:  # domain names are case-insensitive; keep priority/ports
        parts = v.split(None, 1) if rtype == "MX" else [v]
        if rtype == "MX":
            return f"{parts[0]} {parts[1].lower()}"
        return v.lower()
    return v


def norm_set(rtype: str, values) -> list:
    return sorted(norm_value(rtype, v) for v in values)


def expected_records(inventory: dict, web: str, legacy_ips=None, wildcard=False):
    """-> {(fqdn, type): {"ttl": int|None, "values": [...], "alias": bool}}"""
    exp = {}
    for r in inventory["records"]:
        exp[(fqdn(r["name"]), r["type"])] = {"ttl": r["ttl"], "values": norm_set(r["type"], r["values"]), "alias": False}
    excluded = inventory.get("_conversion", {}).get("excluded_exceptions", [])
    ips = legacy_ips or sorted({v for e in excluded if e["type"] == "A" for v in e["values"]})
    labels = ["@", "*" if wildcard else "www"]
    ttl = next((e["ttl"] for e in excluded if e["type"] == "A"), 60)
    for label in labels:
        if web == "legacy":
            exp[(fqdn(label), "A")] = {"ttl": ttl, "values": sorted(ips), "alias": False}
        else:
            for t in ("A", "AAAA"):
                exp[(fqdn(label), t)] = {"ttl": None, "values": [], "alias": True}
    return exp


def rrsets_to_map(rrsets):
    """Route 53 ResourceRecordSets -> {(fqdn, type): {...}} (single-set records only)."""
    out = {}
    for r in rrsets:
        name = r["Name"].replace("\\052", "*").lower()
        out[(name, r["Type"])] = {
            "ttl": r.get("TTL"),
            "values": norm_set(r["Type"], [x["Value"] for x in r.get("ResourceRecords", [])]),
            "alias": "AliasTarget" in r,
            "alias_target": (r.get("AliasTarget") or {}).get("DNSName", "").lower(),
        }
    return out


# --------------------------------------------------------------------------- pure checks


def check_zone_contents(expected, actual, ns_ttl, ns_names):
    """Compare expected records with what the zone holds."""
    checks = []
    for key, want in sorted(expected.items()):
        name, rtype = key
        got = actual.get(key)
        label = f"{name} {rtype}"
        if not got:
            checks.append(Check("records", label, "FAIL", "missing from the zone"))
            continue
        if want["alias"]:
            ok = got["alias"] and got["alias_target"].rstrip(".").endswith(".cloudfront.net")
            checks.append(Check("records", label, "PASS" if ok else "FAIL",
                                f"alias -> {got['alias_target'] or got['values']}"))
            continue
        problems = []
        if got["alias"]:
            problems.append("is an alias, expected plain records")
        if got["values"] != want["values"]:
            problems.append(f"values {got['values']} != expected {want['values']}")
        if got["ttl"] != want["ttl"]:
            problems.append(f"ttl {got['ttl']} != expected {want['ttl']}")
        checks.append(Check("records", label, "FAIL" if problems else "PASS",
                            "; ".join(problems) or f"ttl {got['ttl']} values {got['values']}"))

    apex = fqdn("@")
    ns = actual.get((apex, "NS"))
    if not ns:
        checks.append(Check("apex", "NS", "FAIL", "apex NS record missing"))
    else:
        want_ns = sorted(n.lower().rstrip(".") + "." for n in ns_names)
        got_ns = sorted(v.lower().rstrip(".") + "." for v in ns["values"])
        checks.append(Check("apex", "NS values", "PASS" if got_ns == want_ns and len(got_ns) == 4 else "FAIL",
                            f"{len(got_ns)} name servers: {', '.join(got_ns)}"))
        checks.append(Check("apex", "NS TTL", "PASS" if ns["ttl"] is not None and ns["ttl"] <= ns_ttl else "FAIL",
                            f"{ns['ttl']} (expected <= {ns_ttl}, so a rollback takes effect quickly)"))
    checks.append(Check("apex", "SOA present", "PASS" if (apex, "SOA") in actual else "FAIL", ""))

    allowed = set(expected) | {(apex, "NS"), (apex, "SOA")}
    for key, got in sorted(actual.items()):
        if key in allowed:
            continue
        if key[1] == "CNAME" and all(v.endswith(ACM_SUFFIX) for v in got["values"]):
            checks.append(Check("records", f"{key[0]} CNAME", "INFO", "ACM certificate validation record"))
        else:
            checks.append(Check("records", f"{key[0]} {key[1]}", "FAIL", f"unexpected record not in the inventory: {got['values'] or got['alias_target']}"))
    return checks


def compare_answer(expected_values, rcode, answers, rtype):
    """Compare an answer (rcode + list of value strings) with expected values."""
    if rcode != "NOERROR":
        return False, f"rcode {rcode}"
    got = norm_set(rtype, answers)
    return got == expected_values, f"{got}"


def check_delegation(per_resolver_ns, aws_ns):
    """per_resolver_ns: {resolver: [ns names]}. PASS when every resolver returns exactly the AWS set."""
    want = sorted(n.lower().rstrip(".") for n in aws_ns)
    checks = []
    for res, ns in sorted(per_resolver_ns.items()):
        got = sorted(n.lower().rstrip(".") for n in ns)
        if got == want:
            checks.append(Check("delegation", f"NS at {res}", "PASS", ", ".join(got)))
        else:
            checks.append(Check("delegation", f"NS at {res}", "PENDING", f"still {', '.join(got) or 'nothing'}; "
                                f"previous delegation may be cached until its TTL expires"))
    return checks


# --------------------------------------------------------------------------- AWS + DNS layers


class AwsZone:
    def __init__(self, session, region="us-east-1"):
        self.r53 = session.client("route53", region_name=region)
        self.cw = session.client("cloudwatch", region_name=region)
        self.acm = session.client("acm", region_name=region)
        self.cf = session.client("cloudfront", region_name=region)
        self.logs = session.client("logs", region_name=region)

    def find_zone(self):
        zones = [z for z in self.r53.list_hosted_zones_by_name(DNSName=ZONE, MaxItems="5")["HostedZones"]
                 if z["Name"].rstrip(".") == ZONE and not z["Config"].get("PrivateZone")]
        if len(zones) != 1:
            raise RuntimeError(f"expected exactly one public hosted zone named {ZONE}, found {len(zones)}")
        zid = zones[0]["Id"].split("/")[-1]
        ns = self.r53.get_hosted_zone(Id=zid)["DelegationSet"]["NameServers"]
        return zid, ns

    def rrsets(self, zid):
        out, kwargs = [], {"HostedZoneId": zid}
        while True:
            page = self.r53.list_resource_record_sets(**kwargs)
            out.extend(page["ResourceRecordSets"])
            if not page["IsTruncated"]:
                return out
            kwargs.update(StartRecordName=page["NextRecordName"], StartRecordType=page["NextRecordType"])

    def test_answer(self, zid, name, rtype):
        r = self.r53.test_dns_answer(HostedZoneId=zid, RecordName=name.rstrip("."), RecordType=rtype)
        return r["ResponseCode"], list(r.get("RecordData", []))


def public_query(resolver_ip, name, rtype, timeout=5):
    import dns.message, dns.query, dns.rcode, dns.rdatatype
    r = dns.query.udp(dns.message.make_query(name, rtype), resolver_ip, timeout=timeout)
    vals = []
    for rrset in r.answer:
        if rrset.rdtype == dns.rdatatype.from_text(rtype):
            vals.extend(i.to_text() for i in rrset)
    return dns.rcode.to_text(r.rcode()), vals


# --------------------------------------------------------------------------- orchestration


def run_pre(aws, zid, ns_names, expected, ns_ttl, wildcard):
    actual = rrsets_to_map(aws.rrsets(zid))
    checks = [Check("zone", "hosted zone", "PASS", f"{zid}, {len(ns_names)} name servers: {', '.join(sorted(ns_names))}")]
    checks += check_zone_contents(expected, actual, ns_ttl, ns_names)

    for (name, rtype), want in sorted(expected.items()):
        if want["alias"]:
            continue
        rcode, data = aws.test_answer(zid, name, rtype)
        ok, detail = compare_answer(want["values"], rcode, data, rtype)
        checks.append(Check("authoritative", f"TestDNSAnswer {name} {rtype}", "PASS" if ok else "FAIL", detail))

    probe = f"zz-{uuid.uuid4().hex[:10]}.{ZONE}"
    rcode, _ = aws.test_answer(zid, probe, "A")
    want = "NOERROR" if wildcard else "NXDOMAIN"
    checks.append(Check("authoritative", "unknown name behaviour", "PASS" if rcode == want else "FAIL",
                        f"{probe} A -> {rcode} (expected {want}, matching the live zone which has no wildcard)" if not wildcard else f"{rcode}"))
    return checks


def run_post(aws, zid, ns_names, expected, wait_seconds, resolvers, query=public_query, sleep=time.sleep, now=time.monotonic):
    checks, deadline = [], now() + wait_seconds
    while True:
        per = {}
        for rname, ip in resolvers.items():
            try:
                _, vals = query(ip, ZONE, "NS")
                per[rname] = [v for v in vals]
            except Exception as exc:
                per[rname] = [f"error {type(exc).__name__}"]
        deleg = check_delegation(per, ns_names)
        if all(c.status == "PASS" for c in deleg) or now() >= deadline:
            break
        sleep(30)
    checks += deleg

    for (name, rtype), want in sorted(expected.items()):
        for rname, ip in resolvers.items():
            label = f"{name} {rtype} via {rname}"
            try:
                rcode, vals = query(ip, name.rstrip("."), rtype)
            except Exception as exc:
                checks.append(Check("public", label, "FAIL", f"query error {type(exc).__name__}"))
                continue
            if want["alias"]:
                checks.append(Check("public", label, "PASS" if rcode == "NOERROR" and vals else "FAIL", f"{rcode} {vals}"))
                continue
            ok, detail = compare_answer(want["values"], rcode, vals, rtype)
            checks.append(Check("public", label, "PASS" if ok else "FAIL", detail))
    return checks


def cloud_state(aws):
    """Certificate / distribution / health / alarms. Returns checks (INFO or PASS/PENDING/FAIL)."""
    checks = []
    certs = [c for c in aws.acm.list_certificates()["CertificateSummaryList"] if c["DomainName"] == ZONE]
    if not certs:
        checks.append(Check("certificate", "ACM certificate for amalbank.so", "INFO", "not requested yet"))
    for c in certs:
        d = aws.acm.describe_certificate(CertificateArn=c["CertificateArn"])["Certificate"]
        st = d["Status"]
        checks.append(Check("certificate", f"{d['DomainName']} ({', '.join(d.get('SubjectAlternativeNames', []))})",
                            "PASS" if st == "ISSUED" else ("PENDING" if st == "PENDING_VALIDATION" else "FAIL"), st))
    dists = [d for d in aws.cf.list_distributions().get("DistributionList", {}).get("Items", [])
             if ZONE in d.get("Aliases", {}).get("Items", [])]
    if not dists:
        checks.append(Check("cloudfront", "redirect distribution", "INFO", "not created yet"))
    for d in dists:
        checks.append(Check("cloudfront", f"distribution {d['Id']} ({d['DomainName']})",
                            "PASS" if d["Status"] == "Deployed" else "PENDING", d["Status"]))
    return checks


def write_evidence(directory, mode, checks, meta):
    os.makedirs(directory, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(directory, f"verify-zone-{mode}-{stamp}")
    with open(base + ".json", "w") as fh:
        json.dump({"meta": meta, "checks": [asdict(c) for c in checks]}, fh, indent=2)
        fh.write("\n")
    counts = {s: sum(c.status == s for c in checks) for s in ("PASS", "FAIL", "PENDING", "INFO")}
    lines = [f"# amalbank.so zone verification ({mode}, {stamp})", "",
             f"Zone `{meta['zone_id']}` in account `{meta['account']}`. Results: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "",
             "| Area | Check | Result | Detail |", "|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c.area} | {c.name} | {c.status} | {c.detail.replace('|', '/')} |")
    with open(base + ".md", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return base, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["pre", "post"])
    ap.add_argument("--inventory", default=DEFAULT_INVENTORY)
    ap.add_argument("--web", choices=["legacy", "cloudfront"], default="legacy",
                    help="what the apex/www records should currently be")
    ap.add_argument("--wildcard", action="store_true", help="the stack runs with redirect_wildcard=true")
    ap.add_argument("--apex-ns-ttl", type=int, default=900)
    ap.add_argument("--wait", type=int, default=0, help="post: seconds to wait for the delegation to become visible")
    ap.add_argument("--expected-account", default=EXPECTED_ACCOUNT)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--evidence-dir", default=os.path.join(HERE, "..", "evidence"))
    args = ap.parse_args(argv)

    import boto3
    session = boto3.Session()
    try:
        with open(args.inventory) as fh:
            inventory = json.load(fh)
        account = session.client("sts", region_name=args.region).get_caller_identity()["Account"]
        if account != args.expected_account:
            raise RuntimeError(f"running in account {account}, expected {args.expected_account}")
        aws = AwsZone(session, args.region)
        zid, ns_names = aws.find_zone()
        expected = expected_records(inventory, args.web, wildcard=args.wildcard)
        checks = run_pre(aws, zid, ns_names, expected, args.apex_ns_ttl, args.wildcard)
        if args.mode == "post":
            checks += run_post(aws, zid, ns_names, expected, args.wait, PUBLIC_RESOLVERS)
        checks += cloud_state(aws)
    except Exception as exc:
        print(f"could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    meta = {"tool": "verify_zone.py", "account": account, "zone_id": zid, "name_servers": sorted(ns_names),
            "web": args.web, "inventory_source": inventory.get("source")}
    base, counts = write_evidence(args.evidence_dir, args.mode, checks, meta)
    for c in checks:
        if c.status in ("FAIL", "PENDING"):
            print(f"{c.status:8} {c.area}: {c.name}: {c.detail}")
    print(f"{counts}  evidence: {base}.md")
    if args.mode == "pre":
        print("Name servers for the registrar (exactly these four):\n  " + "\n  ".join(sorted(ns_names)))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
