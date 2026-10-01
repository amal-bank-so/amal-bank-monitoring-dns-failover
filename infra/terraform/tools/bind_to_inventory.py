#!/usr/bin/env python3
"""Convert a BIND zone file into inventory/<zone>.json for the Terraform stack.

Normalises names the way Route 53 needs them and refuses to guess:

* every owner name becomes relative to the zone ("@" = apex, "*" = wildcard);
* every name inside a value (CNAME/MX/NS/SRV target) becomes an absolute FQDN with a
  trailing dot, so nothing can be silently suffixed with the zone name twice;
* names or targets that look like a missing trailing dot are rejected / flagged;
* the same name+type collapses to one record set; differing TTLs inside a set are an
  error (Route 53 has one TTL per set) rather than being silently merged;
* apex NS/SOA are not imported (Route 53 generates them) but are listed in the report
  so the original delegation can be compared and saved;
* unsupported or provider-specific types are listed as exceptions and fail the run
  unless --allow-unsupported is given, so nothing is dropped silently.

The output always has "verified": false. A human flips it to true after reconciling the
report against the authoritative export (see docs/MIGRATION_PLAN.md, Phase 0).

Usage:
  bind_to_inventory.py --zone amalbank.so --input amalbank.so.zone \\
      --source "No-IP export 2026-10-02" --exported-at 2026-10-02T10:00:00Z \\
      --output ../inventory/amalbank.so.json --report report.json

Exit codes: 0 ok, 2 conversion errors (or warnings with --strict), 1 usage/IO error.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from dataclasses import dataclass, field

import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
import dns.zonefile

TOOL_VERSION = "1.0"

# Must match allowed_record_types in ../records.tf (a unit test enforces this).
ALLOWED_TYPES = ["A", "AAAA", "CAA", "CNAME", "MX", "SRV", "TXT", "NS", "DS"]

# Targets of these types are domain names that must be absolute.
NAME_TARGET_TYPES = {"CNAME", "MX", "NS", "SRV"}

# Used only for the "possible missing trailing dot" heuristic on targets.
COMMON_TLDS = {
    "com", "net", "org", "so", "io", "co", "info", "biz", "int", "gov", "edu",
    "uk", "us", "eu", "de", "fr", "nl", "ae", "sa", "ke", "et", "dj", "cloud", "online",
}

_COMMENT = (
    "Verified authoritative amalbank.so inventory, populated in Phase 0 from the live "
    "No-IP export (NOT from the DigiCert view). 'name' is relative to the zone ('@' = "
    "apex, '*' = wildcard); never include the zone name or a trailing dot. 'values' use "
    "Route 53 syntax (TXT values include their double quotes; MX is 'priority host.'; "
    "CNAME/MX targets are FQDNs with a trailing dot). Do not list apex NS/SOA: AWS "
    "generates them."
)


@dataclass
class Result:
    zone: str
    records: list = field(default_factory=list)
    skipped_apex: list = field(default_factory=list)  # apex NS/SOA, for the report only
    unsupported: list = field(default_factory=list)
    child_delegations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def ok(self, strict: bool = False, allow_unsupported: bool = False) -> bool:
        if self.errors:
            return False
        if self.unsupported and not allow_unsupported:
            return False
        if strict and self.warnings:
            return False
        return True


class _Collector:
    """Minimal stand-in for the zone transaction dns.zonefile.Reader writes into.

    Collecting records ourselves (instead of building a dns.zone.Zone) means:
    * each record keeps its own TTL (dnspython merges a set and silently keeps the
      lowest);
    * owners outside the zone are visible (dnspython silently skips them when it knows
      the zone origin), so they can be rejected explicitly;
    * an SOA that is not at the root does not abort parsing.
    The Reader only touches: manager.origin_information(), check_put_rdataset(), add()
    and _set_origin(). The dnspython version is pinned in requirements.txt and the unit
    tests exercise all of this.
    """

    def __init__(self):
        self.seen = []
        self.manager = self

    def origin_information(self):
        # (zone origin, relativize, effective origin): read everything absolute.
        return (dns.name.root, False, dns.name.root)

    def check_put_rdataset(self, check):
        pass

    def _set_origin(self, origin):
        pass

    def add(self, name, ttl, rd):
        self.seen.append((name, ttl, rd))


def _read_records(text: str, origin: dns.name.Name, default_ttl: int | None):
    collector = _Collector()
    reader = dns.zonefile.Reader(
        dns.tokenizer.Tokenizer(text),
        dns.rdataclass.IN,
        collector,
        allow_include=False,
        allow_directives=True,
        default_ttl=default_ttl,
    )
    # Start relative names at the real origin; the reader's own zone origin is the root.
    reader.current_origin = origin
    reader.last_name = origin
    reader.read()
    return collector.seen


def _is_suspicious_target(target: dns.name.Name, origin: dns.name.Name) -> bool:
    """A target that sits inside the zone but whose relative part looks like a complete
    external hostname, e.g. 'autodiscover.outlook.com' written without a trailing dot
    in a zone file, which BIND turns into autodiscover.outlook.com.<zone>."""
    if not target.is_subdomain(origin) or target == origin:
        return False
    rel = target.relativize(origin).labels
    if len(rel) < 3:  # 'x.y' relative part is the minimum that could be 'host.tld'
        return False
    last = rel[-1].decode("ascii", "replace").lower()
    return last in COMMON_TLDS


def convert(text: str, zone: str, default_ttl: int | None = None) -> Result:
    zone = zone.rstrip(".").lower()
    origin = dns.name.from_text(zone + ".")
    res = Result(zone=zone)
    zone_labels = tuple(l.lower() for l in origin.labels[:-1])

    try:
        seen = _read_records(text, origin, default_ttl)
    except Exception as exc:  # dnspython raises several exception types
        res.errors.append(f"zone file could not be parsed: {type(exc).__name__}: {exc}")
        return res

    groups: dict = collections.OrderedDict()
    for owner, ttl, rd in seen:
        rtype = dns.rdatatype.to_text(rd.rdtype)
        if not owner.is_subdomain(origin):
            res.errors.append(f"{owner} {rtype}: owner name is outside the zone {zone}")
            continue

        rel = owner.relativize(origin)
        rel_labels = tuple(l.lower() for l in rel.labels if l)
        is_apex = owner == origin
        rel_text = "@" if is_apex else ".".join(
            l.decode("ascii", "replace").lower() for l in rel.labels
        )

        if rel_labels and rel_labels[-len(zone_labels):] == zone_labels:
            res.errors.append(
                f"{rel_text} {rtype}: owner name ends with the zone name "
                f"'{zone}', so the source almost certainly omitted a trailing dot "
                f"(it would become {rel_text}.{zone})"
            )
            continue

        value = rd.to_text(origin=None, relativize=False)

        if rtype in ("SOA",) or (rtype == "NS" and is_apex):
            res.skipped_apex.append({"type": rtype, "ttl": ttl, "value": value})
            continue

        if rtype not in ALLOWED_TYPES:
            res.unsupported.append(
                {"name": rel_text, "type": rtype, "ttl": ttl, "value": value}
            )
            continue

        if rtype == "CNAME" and is_apex:
            res.errors.append("@ CNAME: a CNAME at the zone apex is not valid in Route 53")
            continue

        if rtype in NAME_TARGET_TYPES:
            target = getattr(rd, "target", None) or getattr(rd, "exchange", None)
            if target is not None and _is_suspicious_target(target, origin):
                res.warnings.append(
                    f"{rel_text} {rtype}: target '{target}' is inside {zone} but looks like "
                    f"an external hostname; was a trailing dot omitted in the source?"
                )

        if rtype == "NS":
            res.child_delegations.append({"name": rel_text, "value": value})

        key = (rel_text, rtype)
        g = groups.setdefault(key, {"ttls": set(), "values": []})
        g["ttls"].add(ttl)
        if value not in g["values"]:
            g["values"].append(value)

    types_by_name = collections.defaultdict(set)
    for (name, rtype) in groups:
        types_by_name[name].add(rtype)
    for name, types in types_by_name.items():
        if "CNAME" in types and len(types) > 1:
            res.errors.append(
                f"{name}: a CNAME cannot coexist with other record types ({sorted(types - {'CNAME'})})"
            )

    for (name, rtype), g in groups.items():
        if len(g["ttls"]) > 1:
            res.errors.append(
                f"{name} {rtype}: records in one set have different TTLs "
                f"{sorted(g['ttls'])}; Route 53 has one TTL per set. Decide the value and "
                f"correct the export."
            )
            continue
        res.records.append(
            {"name": name, "type": rtype, "ttl": next(iter(g["ttls"])), "values": g["values"]}
        )

    # Records below a child delegation are occluded (not served by this zone's servers).
    deleg = [d["name"] for d in res.child_delegations]
    for r in res.records:
        for d in deleg:
            if r["name"] != d and r["name"].endswith("." + d) and r["type"] != "NS":
                res.warnings.append(
                    f"{r['name']} {r['type']}: sits below the child delegation '{d}' "
                    f"(occluded; review before import)"
                )

    order = {t: i for i, t in enumerate(ALLOWED_TYPES)}
    res.records.sort(key=lambda r: (r["name"] != "@", r["name"], order[r["type"]]))
    return res


def build_inventory(res: Result, source, exported_at, input_sha256: str) -> dict:
    return {
        "_comment": _COMMENT,
        "verified": False,
        "source": source,
        "exported_at": exported_at,
        "_conversion": {
            "tool": f"bind_to_inventory.py {TOOL_VERSION}",
            "zone": res.zone,
            "input_sha256": input_sha256,
            "record_sets": len(res.records),
            "values": sum(len(r["values"]) for r in res.records),
            "skipped_apex_ns_soa": len(res.skipped_apex),
            "unsupported": len(res.unsupported),
            "warnings": len(res.warnings),
        },
        "records": res.records,
    }


def build_report(res: Result, input_sha256: str) -> dict:
    by_type = collections.Counter(r["type"] for r in res.records)
    return {
        "zone": res.zone,
        "input_sha256": input_sha256,
        "record_sets": len(res.records),
        "by_type": dict(sorted(by_type.items())),
        "skipped_apex_ns_soa": res.skipped_apex,
        "child_delegations": res.child_delegations,
        "unsupported": res.unsupported,
        "warnings": res.warnings,
        "errors": res.errors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", required=True, help="zone name, e.g. amalbank.so")
    ap.add_argument("--input", required=True, help="BIND zone file ('-' = stdin)")
    ap.add_argument("--output", help="inventory JSON to write (omit for a dry run)")
    ap.add_argument("--report", help="write the reconciliation report JSON here")
    ap.add_argument("--source", help="where the export came from (recorded in the inventory)")
    ap.add_argument("--exported-at", help="ISO-8601 export time (recorded in the inventory)")
    ap.add_argument("--default-ttl", type=int, help="TTL for records with none and no $TTL in the file")
    ap.add_argument("--allow-unsupported", action="store_true",
                    help="succeed even if unsupported record types exist (they stay listed as exceptions)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)

    try:
        raw = sys.stdin.buffer.read() if args.input == "-" else open(args.input, "rb").read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 1
    sha = hashlib.sha256(raw).hexdigest()

    res = convert(text, args.zone, args.default_ttl)
    report = build_report(res, sha)

    for e in res.errors:
        print(f"ERROR   {e}", file=sys.stderr)
    for u in res.unsupported:
        print(f"UNSUPPORTED {u['name']} {u['type']} {u['value']}", file=sys.stderr)
    for w in res.warnings:
        print(f"WARNING {w}", file=sys.stderr)

    ok = res.ok(strict=args.strict, allow_unsupported=args.allow_unsupported)
    print(
        f"{res.zone}: {len(res.records)} record sets, {len(res.skipped_apex)} apex NS/SOA skipped, "
        f"{len(res.unsupported)} unsupported, {len(res.warnings)} warnings, {len(res.errors)} errors",
        file=sys.stderr,
    )

    if args.report:
        with open(args.report, "w") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")

    if not ok:
        print("not writing inventory: fix the issues above", file=sys.stderr)
        return 2

    if args.output:
        inv = build_inventory(res, args.source, args.exported_at, sha)
        with open(args.output, "w") as fh:
            json.dump(inv, fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.output} (verified=false; set it to true only after reconciliation)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
