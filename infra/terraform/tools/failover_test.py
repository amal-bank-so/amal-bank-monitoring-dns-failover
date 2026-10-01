#!/usr/bin/env python3
"""Exercise Route 53 failover on the isolated test record pair, never on live endpoints.

Requires the Terraform stack applied with enable_failover_test=true. That creates
failover-test.ebanking.amalbankso.com with PRIMARY/SECONDARY records on TEST-NET-1
addresses whose health checks follow CloudWatch alarms. This driver sets each target's
simulated health by publishing a high-resolution custom metric, asks Route 53 what it
would answer (route53:TestDNSAnswer, so the zone need not be delegated), and records how
long every change takes to show up.

Scenarios (each state change is observable in the answer except the last two):
  1 baseline          primary up,   secondary up    -> primary
  2 primary fails     primary down, secondary up    -> secondary   (failover)
  3 both down         primary down, secondary down  -> primary     (Route 53 serves the primary when both fail)
  4 secondary back    primary down, secondary up    -> secondary
  5 primary back      primary up,   secondary up    -> primary     (failback)
  6 secondary fails   primary up,   secondary down  -> primary     (no routing change; applied but not observable)
  7 secondary back    primary up,   secondary up    -> primary     (restore)

Safety: refuses to run unless the record name starts with "failover-test.", every IP is in
192.0.2.0/24, every health check is CLOUDWATCH_METRIC and tied to an alarm named
<prefix>-failover-test-*, and those alarms have no actions. The live ebanking records,
health checks and alarms are never touched. On exit (including errors) both targets are
set healthy and the answer is confirmed back to the primary.

Credentials come from the normal AWS environment (. tools/aws-env.sh in this repo).
Exit codes: 0 all scenarios passed, 1 a scenario failed, 2 refused/preflight problem.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict

TOOL_VERSION = "1.0"
ZONE_NAME = "ebanking.amalbankso.com"
TEST_NAME = f"failover-test.{ZONE_NAME}"
TEST_NET = ipaddress.ip_network("192.0.2.0/24")
NAMESPACE = "AmalDnsTest"
METRIC = "Unhealthy"
EXPECTED_ACCOUNT = "029288159395"
TARGETS = ("primary", "secondary")


class SafetyError(Exception):
    """The environment is not the isolated test setup; refuse to run."""


@dataclass(frozen=True)
class Step:
    name: str
    primary_up: bool
    secondary_up: bool
    expect: str  # "primary" | "secondary"
    observable: bool
    note: str


SCENARIOS = [
    Step("baseline", True, True, "primary", False, "initial state; confirms the test pair serves the primary"),
    Step("primary-fails", False, True, "secondary", True, "failover"),
    Step("both-down", False, False, "primary", True, "Route 53 returns the primary when both records are unhealthy"),
    Step("secondary-recovers", False, True, "secondary", True, "secondary healthy again while primary still down"),
    Step("primary-recovers", True, True, "primary", True, "failback to the primary"),
    Step("secondary-fails", True, False, "primary", False, "primary stays preferred; change applied but routing is unchanged, so not observable in the answer"),
    Step("secondary-recovers-final", True, True, "primary", False, "restore"),
]


def expected_answer(primary_up: bool, secondary_up: bool) -> str:
    """Documented Route 53 failover behaviour with health checks on both records."""
    if primary_up:
        return "primary"
    return "secondary" if secondary_up else "primary"


# --------------------------------------------------------------------------- safety


def validate_topology(zone_name, record_sets, health_checks, alarms, name_prefix):
    """Raise SafetyError unless this is exactly the isolated test pair.

    record_sets: Route 53 ResourceRecordSets for TEST_NAME type A.
    health_checks: {health_check_id: HealthCheck dict}.
    alarms: {alarm_name: MetricAlarm dict}.
    Returns {"primary": {...}, "secondary": {...}} with ip, health_check_id, alarm_name.
    """
    if zone_name.rstrip(".") != ZONE_NAME:
        raise SafetyError(f"hosted zone is {zone_name!r}, expected {ZONE_NAME!r}")
    if not TEST_NAME.startswith("failover-test."):
        raise SafetyError("internal: test name must start with failover-test.")  # pragma: no cover
    mine = [r for r in record_sets if r["Name"].rstrip(".").lower() == TEST_NAME and r["Type"] == "A"]
    if len(mine) != 2:
        raise SafetyError(
            f"expected 2 A records for {TEST_NAME}, found {len(mine)}. "
            f"Apply the stack with enable_failover_test=true first."
        )
    out = {}
    for r in mine:
        role = (r.get("Failover") or "").lower()
        if role not in TARGETS:
            raise SafetyError(f"{r['Name']} {r.get('SetIdentifier')}: not a failover record")
        if len(r.get("ResourceRecords", [])) != 1:
            raise SafetyError(f"{role}: expected exactly one IP")
        ip = r["ResourceRecords"][0]["Value"]
        if ipaddress.ip_address(ip) not in TEST_NET:
            raise SafetyError(f"{role}: {ip} is not in {TEST_NET}; refusing to touch a possibly live address")
        hc_id = r.get("HealthCheckId")
        hc = health_checks.get(hc_id)
        if not hc:
            raise SafetyError(f"{role}: health check {hc_id} not found")
        cfg = hc["HealthCheckConfig"]
        if cfg.get("Type") != "CLOUDWATCH_METRIC":
            raise SafetyError(f"{role}: health check type is {cfg.get('Type')}, expected CLOUDWATCH_METRIC")
        alarm_name = (cfg.get("AlarmIdentifier") or {}).get("Name", "")
        if not alarm_name.startswith(f"{name_prefix}-failover-test-"):
            raise SafetyError(f"{role}: alarm {alarm_name!r} is not a {name_prefix}-failover-test-* alarm")
        alarm = alarms.get(alarm_name)
        if not alarm:
            raise SafetyError(f"{role}: alarm {alarm_name} not found")
        if alarm.get("AlarmActions") or alarm.get("OKActions") or alarm.get("InsufficientDataActions"):
            raise SafetyError(f"{role}: alarm {alarm_name} has actions; a test must never notify anyone")
        if alarm.get("Namespace") != NAMESPACE or alarm.get("MetricName") != METRIC:
            raise SafetyError(f"{role}: alarm {alarm_name} is not on {NAMESPACE}/{METRIC}")
        dims = {d["Name"]: d["Value"] for d in alarm.get("Dimensions", [])}
        if dims.get("Target") != role:
            raise SafetyError(f"{role}: alarm dimension Target is {dims.get('Target')!r}")
        out[role] = {"ip": ip, "health_check_id": hc_id, "alarm_name": alarm_name}
    if set(out) != set(TARGETS):
        raise SafetyError("need one PRIMARY and one SECONDARY record")
    return out


# --------------------------------------------------------------------------- runner


class Clock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def utc(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepResult:
    step: str
    desired: dict
    expected: str
    observed: list = field(default_factory=list)
    passed: bool = False
    observable: bool = True
    seconds_to_converge: float | None = None
    started: str = ""
    nameserver: str | None = None
    note: str = ""
    error: str | None = None


def run_scenarios(backend, clock, ips, poll_interval=5.0, timeout=600.0, stable_checks=3, log=print):
    """Run every scenario against *backend*. Always restores both targets to healthy.

    backend: set_health(primary_up, secondary_up), answer() -> (list_of_ips, nameserver).
    ips: {"primary": ip, "secondary": ip}.
    """
    results = []
    try:
        for step in SCENARIOS:
            assert step.expect == expected_answer(step.primary_up, step.secondary_up), step
            want_ip = ips[step.expect]
            res = StepResult(
                step=step.name,
                desired={"primary": "up" if step.primary_up else "down",
                         "secondary": "up" if step.secondary_up else "down"},
                expected=step.expect, observable=step.observable, note=step.note,
                started=clock.utc(),
            )
            results.append(res)
            log(f"[{step.name}] primary={res.desired['primary']} secondary={res.desired['secondary']} "
                f"-> expect {step.expect} ({want_ip})")
            t0 = clock.now()
            stable = 0
            while True:
                backend.set_health(step.primary_up, step.secondary_up)  # re-assert every poll
                try:
                    observed, ns = backend.answer()
                except Exception as exc:  # transient API error: keep polling until timeout
                    res.error = f"{type(exc).__name__}: {exc}"
                    observed, ns = [], None
                res.observed, res.nameserver = observed, ns or res.nameserver
                elapsed = clock.now() - t0
                if observed == [want_ip]:
                    stable += 1
                    if res.seconds_to_converge is None:
                        res.seconds_to_converge = round(elapsed, 1)
                    if stable >= stable_checks:
                        res.passed = True
                        res.error = None
                        break
                else:
                    stable = 0
                    res.seconds_to_converge = None
                if elapsed >= timeout:
                    break
                clock.sleep(poll_interval)
            log(f"    {'PASS' if res.passed else 'FAIL'}: observed {res.observed}"
                + (f" after {res.seconds_to_converge}s" if res.seconds_to_converge is not None else ""))
            if not res.passed:
                break  # later scenarios depend on this state; stop and restore
    finally:
        _restore(backend, clock, ips, poll_interval, timeout, log, results)
    return results


def _restore(backend, clock, ips, poll_interval, timeout, log, results):
    log("[restore] both targets healthy")
    t0 = clock.now()
    ok = False
    while True:
        try:
            backend.set_health(True, True)
            observed, _ = backend.answer()
            ok = observed == [ips["primary"]]
        except Exception as exc:
            log(f"    restore check error: {type(exc).__name__}: {exc}")
        if ok or clock.now() - t0 >= timeout:
            break
        clock.sleep(poll_interval)
    log(f"    restore {'confirmed' if ok else 'NOT confirmed (check alarms manually)'}")
    results.append(StepResult(step="restore", desired={"primary": "up", "secondary": "up"},
                              expected="primary", observed=[ips["primary"]] if ok else [],
                              passed=ok, observable=False, note="cleanup"))


# --------------------------------------------------------------------------- AWS backend


class AwsBackend:
    def __init__(self, session, region, zone_id):
        self.r53 = session.client("route53", region_name=region)
        self.cw = session.client("cloudwatch", region_name=region)
        self.zone_id = zone_id

    def set_health(self, primary_up: bool, secondary_up: bool) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        self.cw.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[
                {
                    "MetricName": METRIC,
                    "Dimensions": [{"Name": "Target", "Value": role}],
                    "Timestamp": now,
                    "Value": 0.0 if up else 1.0,
                    "Unit": "None",
                    "StorageResolution": 1,
                }
                for role, up in (("primary", primary_up), ("secondary", secondary_up))
            ],
        )

    def answer(self):
        r = self.r53.test_dns_answer(HostedZoneId=self.zone_id, RecordName=TEST_NAME, RecordType="A")
        if r.get("ResponseCode") != "NOERROR":
            raise RuntimeError(f"TestDNSAnswer response code {r.get('ResponseCode')}")
        return list(r.get("RecordData", [])), r.get("Nameserver")


def discover(session, region, zone_id, name_prefix):
    """Find the zone and test pair; return (zone_id, topology) or raise SafetyError."""
    r53 = session.client("route53", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    if not zone_id:
        zones = [z for z in r53.list_hosted_zones_by_name(DNSName=ZONE_NAME, MaxItems="5")["HostedZones"]
                 if z["Name"].rstrip(".") == ZONE_NAME and not z["Config"].get("PrivateZone")]
        if len(zones) != 1:
            raise SafetyError(f"expected exactly one public zone named {ZONE_NAME}, found {len(zones)}")
        zone_id = zones[0]["Id"].split("/")[-1]
    zone = r53.get_hosted_zone(Id=zone_id)["HostedZone"]
    # Both records share name and type, so they are adjacent at the start of this listing.
    records = r53.list_resource_record_sets(
        HostedZoneId=zone_id, StartRecordName=TEST_NAME, StartRecordType="A", MaxItems="10"
    )["ResourceRecordSets"]
    records = [r for r in records if r["Name"].rstrip(".").lower() == TEST_NAME and r["Type"] == "A"]
    hcs = {}
    for r in records:
        if r.get("HealthCheckId"):
            hcs[r["HealthCheckId"]] = r53.get_health_check(HealthCheckId=r["HealthCheckId"])["HealthCheck"]
    names = [(h["HealthCheckConfig"].get("AlarmIdentifier") or {}).get("Name") for h in hcs.values()]
    alarms = {}
    if any(names):
        for a in cw.describe_alarms(AlarmNames=[n for n in names if n])["MetricAlarms"]:
            alarms[a["AlarmName"]] = a
    topo = validate_topology(zone["Name"], records, hcs, alarms, name_prefix)
    return zone_id, topo


# --------------------------------------------------------------------------- reporting


def write_evidence(directory, results, meta):
    os.makedirs(directory, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(directory, f"failover-test-{stamp}")
    doc = {"meta": meta, "results": [asdict(r) for r in results]}
    with open(base + ".json", "w") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    lines = [
        f"# Failover test evidence ({stamp})", "",
        f"- Account: `{meta.get('account')}`  Zone: `{meta.get('zone_id')}` ({ZONE_NAME})",
        f"- Test name: `{TEST_NAME}`  Targets: {meta.get('ips')}",
        f"- Poll interval: {meta['poll_interval']}s, timeout per step: {meta['timeout']}s, "
        f"stable checks: {meta['stable_checks']}", "",
        "| Step | Primary | Secondary | Expected | Observed | Seconds to converge | Result | Note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        res = "PASS" if r.passed else "FAIL"
        if r.passed and not r.observable:
            res = "PASS (applied; not observable)"
        lines.append(
            f"| {r.step} | {r.desired['primary']} | {r.desired['secondary']} | {r.expected} | "
            f"{','.join(r.observed) or '-'} | {r.seconds_to_converge if r.seconds_to_converge is not None else '-'} | {res} | {r.note} |"
        )
    with open(base + ".md", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone-id", help="ebanking hosted zone ID (default: discover by name)")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--name-prefix", default="amal-dns", help="matches var.name_prefix in Terraform")
    ap.add_argument("--expected-account", default=EXPECTED_ACCOUNT)
    ap.add_argument("--poll-interval", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for each state change")
    ap.add_argument("--stable-checks", type=int, default=3, help="consecutive matching answers required")
    ap.add_argument("--evidence-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evidence"))
    ap.add_argument("--dry-run", action="store_true", help="print the scenarios and exit without calling AWS")
    args = ap.parse_args(argv)

    if args.dry_run:
        for i, s in enumerate(SCENARIOS, 1):
            print(f"{i}. {s.name:26} primary={'up' if s.primary_up else 'down':4} "
                  f"secondary={'up' if s.secondary_up else 'down':4} -> {s.expect:9} {s.note}")
        return 0

    import boto3  # imported late so --dry-run and the unit tests need no AWS libraries

    session = boto3.Session()
    try:
        account = session.client("sts", region_name=args.region).get_caller_identity()["Account"]
        if account != args.expected_account:
            raise SafetyError(f"running in account {account}, expected {args.expected_account}")
        zone_id, topo = discover(session, args.region, args.zone_id, args.name_prefix)
    except SafetyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    ips = {role: topo[role]["ip"] for role in TARGETS}
    print(f"account {account}, zone {zone_id}, test pair {TEST_NAME}: {ips}")
    backend = AwsBackend(session, args.region, zone_id)
    results = run_scenarios(backend, Clock(), ips, args.poll_interval, args.timeout, args.stable_checks)

    meta = {"tool": f"failover_test.py {TOOL_VERSION}", "account": account, "zone_id": zone_id, "ips": ips,
            "poll_interval": args.poll_interval, "timeout": args.timeout, "stable_checks": args.stable_checks,
            "finished": Clock().utc()}
    base = write_evidence(args.evidence_dir, results, meta)
    print(f"evidence written to {base}.json / .md")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
