"""Unit tests for failover_test.py using a simulated Route 53 and a virtual clock.
Run: python -m unittest discover -s infra/tools"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import failover_test as ft  # noqa: E402

IPS = {"primary": "192.0.2.10", "secondary": "192.0.2.20"}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def utc(self):
        return f"2026-10-02T00:00:{int(self.t) % 60:02d}+00:00"


class FakeRoute53:
    """Applies the documented failover rules to health states that reach 'Route 53'
    only after `lag` seconds, like a real CloudWatch-driven health check."""

    def __init__(self, clock, lag=30.0, broken=None):
        self.clock, self.lag, self.broken = clock, lag, broken
        self.history = [(-1e9, True, True)]  # (time, primary_up, secondary_up)
        self.set_calls = []
        self.answer_calls = 0
        self.fail_answers = 0
        self.raise_on_set = None

    def set_health(self, p, s):
        self.set_calls.append((p, s))
        if self.raise_on_set and (p, s) == self.raise_on_set:
            raise RuntimeError("boom")
        if self.history[-1][1:] != (p, s):
            self.history.append((self.clock.now(), p, s))

    def effective(self):
        t = self.clock.now() - self.lag
        state = self.history[0][1:]
        for ts, p, s in self.history:
            if ts <= t:
                state = (p, s)
        return state

    def answer(self):
        self.answer_calls += 1
        if self.fail_answers > 0:
            self.fail_answers -= 1
            raise RuntimeError("throttled")
        p, s = self.effective()
        who = ft.expected_answer(p, s)
        if self.broken == "no-failover":
            who = "primary"
        if self.broken == "both-down-secondary" and not p and not s:
            who = "secondary"
        return [IPS[who]], "ns-1.awsdns-00.com"


def run(fake, **kw):
    clock = fake.clock
    logs = []
    res = ft.run_scenarios(fake, clock, IPS, poll_interval=kw.pop("poll_interval", 5.0),
                           timeout=kw.pop("timeout", 300.0), stable_checks=kw.pop("stable_checks", 3),
                           log=logs.append)
    return res, logs


class Scenarios(unittest.TestCase):
    def test_expected_answer_table(self):
        self.assertEqual(ft.expected_answer(True, True), "primary")
        self.assertEqual(ft.expected_answer(True, False), "primary")
        self.assertEqual(ft.expected_answer(False, True), "secondary")
        self.assertEqual(ft.expected_answer(False, False), "primary")

    def test_every_observable_step_changes_the_expected_answer(self):
        prev = None
        for s in ft.SCENARIOS:
            if s.observable:
                self.assertNotEqual(s.expect, prev, f"{s.name} would pass vacuously")
            prev = s.expect

    def test_scenarios_cover_failover_failback_and_both_down(self):
        names = [s.name for s in ft.SCENARIOS]
        for n in ("primary-fails", "both-down", "primary-recovers"):
            self.assertIn(n, names)

    def test_full_run_passes_and_measures_convergence(self):
        clock = FakeClock()
        res, _ = run(FakeRoute53(clock, lag=30))
        self.assertTrue(all(r.passed for r in res), [(r.step, r.observed) for r in res if not r.passed])
        self.assertEqual([r.step for r in res][-1], "restore")
        by = {r.step: r for r in res}
        for name in ("primary-fails", "both-down", "secondary-recovers", "primary-recovers"):
            self.assertGreaterEqual(by[name].seconds_to_converge, 30)
            self.assertLessEqual(by[name].seconds_to_converge, 35)
        self.assertEqual(by["both-down"].observed, [IPS["primary"]])

    def test_unobservable_steps_are_flagged(self):
        res, _ = run(FakeRoute53(FakeClock(), lag=10))
        by = {r.step: r for r in res}
        self.assertFalse(by["secondary-fails"].observable)
        self.assertTrue(by["primary-fails"].observable)

    def test_failover_that_never_happens_fails_and_stops(self):
        clock = FakeClock()
        fake = FakeRoute53(clock, lag=10, broken="no-failover")
        res, _ = run(fake, timeout=60)
        failed = [r for r in res if not r.passed]
        self.assertEqual(failed[0].step, "primary-fails")
        self.assertNotIn("both-down", [r.step for r in res])  # stopped after the failure
        self.assertEqual(res[-1].step, "restore")
        self.assertEqual(fake.set_calls[-1], (True, True))

    def test_both_down_serving_the_secondary_is_a_failure(self):
        res, _ = run(FakeRoute53(FakeClock(), lag=10, broken="both-down-secondary"), timeout=60)
        self.assertEqual([r.step for r in res if not r.passed][0], "both-down")

    def test_health_is_reasserted_every_poll(self):
        fake = FakeRoute53(FakeClock(), lag=30)
        run(fake)
        self.assertGreater(len(fake.set_calls), 20)

    def test_transient_api_errors_are_retried(self):
        fake = FakeRoute53(FakeClock(), lag=5)
        fake.fail_answers = 4
        res, _ = run(fake)
        self.assertTrue(all(r.passed for r in res))

    def test_flapping_answer_resets_stability_counter(self):
        clock = FakeClock()
        fake = FakeRoute53(clock, lag=0)
        orig = fake.answer
        script = [[IPS["primary"]], [IPS["secondary"]]]  # right, wrong, then the real answers
        calls = {"n": 0}

        def flappy():
            calls["n"] += 1
            return (script[calls["n"] - 1], "ns") if calls["n"] <= len(script) else orig()

        fake.answer = flappy
        passed_at = []

        def log(line):
            if line.strip().startswith("PASS") and not passed_at:
                passed_at.append(calls["n"])

        ft.run_scenarios(fake, clock, IPS, poll_interval=5.0, timeout=300, stable_checks=2, log=log)
        # calls 3 and 4 are the first two consecutive correct answers; without the
        # reset the baseline would have passed at call 3.
        self.assertEqual(passed_at, [4])

    def test_restore_runs_even_if_the_backend_raises(self):
        fake = FakeRoute53(FakeClock(), lag=5)
        fake.raise_on_set = (False, False)
        with self.assertRaises(RuntimeError):
            run(fake)
        self.assertEqual(fake.set_calls[-1], (True, True))
        self.assertIn((True, True), fake.set_calls[-3:])


def good_topology():
    records = [
        {"Name": "failover-test.ebanking.amalbankso.com.", "Type": "A", "SetIdentifier": "failover-test-primary",
         "Failover": "PRIMARY", "TTL": 60, "ResourceRecords": [{"Value": "192.0.2.10"}], "HealthCheckId": "hc-p"},
        {"Name": "failover-test.ebanking.amalbankso.com.", "Type": "A", "SetIdentifier": "failover-test-secondary",
         "Failover": "SECONDARY", "TTL": 60, "ResourceRecords": [{"Value": "192.0.2.20"}], "HealthCheckId": "hc-s"},
    ]
    hcs = {
        "hc-p": {"HealthCheckConfig": {"Type": "CLOUDWATCH_METRIC", "AlarmIdentifier": {"Region": "us-east-1", "Name": "amal-dns-failover-test-primary"}}},
        "hc-s": {"HealthCheckConfig": {"Type": "CLOUDWATCH_METRIC", "AlarmIdentifier": {"Region": "us-east-1", "Name": "amal-dns-failover-test-secondary"}}},
    }
    alarms = {
        f"amal-dns-failover-test-{r}": {"AlarmName": f"amal-dns-failover-test-{r}", "Namespace": "AmalDnsTest",
                                        "MetricName": "Unhealthy", "Dimensions": [{"Name": "Target", "Value": r}],
                                        "AlarmActions": [], "OKActions": [], "InsufficientDataActions": []}
        for r in ("primary", "secondary")
    }
    return "ebanking.amalbankso.com.", records, hcs, alarms


class Safety(unittest.TestCase):
    def check(self, mutate):
        zone, recs, hcs, alarms = copy.deepcopy(good_topology())
        zone, recs, hcs, alarms = mutate(zone, recs, hcs, alarms) or (zone, recs, hcs, alarms)
        return ft.validate_topology(zone, recs, hcs, alarms, "amal-dns")

    def test_good_topology_accepted(self):
        topo = self.check(lambda *a: None)
        self.assertEqual(topo["primary"]["ip"], "192.0.2.10")
        self.assertEqual(topo["secondary"]["alarm_name"], "amal-dns-failover-test-secondary")

    def refuses(self, mutate, text):
        with self.assertRaises(ft.SafetyError) as cm:
            self.check(mutate)
        self.assertIn(text, str(cm.exception))

    def test_wrong_zone(self):
        self.refuses(lambda z, r, h, a: ("amalbankso.com.", r, h, a), "expected 'ebanking.amalbankso.com'")

    def test_live_ip_refused(self):
        def m(z, r, h, a):
            r[0]["ResourceRecords"][0]["Value"] = "37.34.133.35"
        self.refuses(m, "not in 192.0.2.0/24")

    def test_missing_record_pair(self):
        self.refuses(lambda z, r, h, a: (z, r[:1], h, a), "expected 2 A records")

    def test_non_failover_record(self):
        def m(z, r, h, a):
            del r[0]["Failover"]
        self.refuses(m, "not a failover record")

    def test_real_tcp_health_check_refused(self):
        def m(z, r, h, a):
            h["hc-p"]["HealthCheckConfig"]["Type"] = "TCP"
        self.refuses(m, "expected CLOUDWATCH_METRIC")

    def test_live_alarm_refused(self):
        def m(z, r, h, a):
            h["hc-p"]["HealthCheckConfig"]["AlarmIdentifier"]["Name"] = "amal-dns-ebanking-primary-unhealthy"
        self.refuses(m, "failover-test-* alarm")

    def test_alarm_with_actions_refused(self):
        def m(z, r, h, a):
            a["amal-dns-failover-test-primary"]["AlarmActions"] = ["arn:aws:sns:us-east-1:1:topic"]
        self.refuses(m, "has actions")

    def test_wrong_metric_or_dimension(self):
        def m1(z, r, h, a):
            a["amal-dns-failover-test-primary"]["MetricName"] = "HealthCheckStatus"
        self.refuses(m1, "not on AmalDnsTest/Unhealthy")

        def m2(z, r, h, a):
            a["amal-dns-failover-test-primary"]["Dimensions"][0]["Value"] = "secondary"
        self.refuses(m2, "dimension Target")

    def test_missing_health_check(self):
        def m(z, r, h, a):
            h.pop("hc-s")
        self.refuses(m, "health check hc-s not found")


class Evidence(unittest.TestCase):
    def test_evidence_files_written(self):
        clock = FakeClock()
        res, _ = run(FakeRoute53(clock, lag=10))
        meta = {"account": "029288159395", "zone_id": "Z123", "ips": IPS, "poll_interval": 5, "timeout": 300, "stable_checks": 3}
        with tempfile.TemporaryDirectory() as d:
            base = ft.write_evidence(d, res, meta)
            with open(base + ".json") as fh:
                doc = json.load(fh)
            with open(base + ".md") as fh:
                md = fh.read()
        self.assertEqual(len(doc["results"]), len(ft.SCENARIOS) + 1)
        self.assertIn("| both-down |", md)
        self.assertIn("PASS (applied; not observable)", md)


class Cli(unittest.TestCase):
    def test_dry_run_lists_scenarios_without_aws(self):
        p = subprocess.run([sys.executable, os.path.join(HERE, "failover_test.py"), "--dry-run"],
                           capture_output=True, text=True, env={k: v for k, v in os.environ.items() if not k.startswith("AWS_")})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(p.stdout.strip().splitlines()), len(ft.SCENARIOS))
        self.assertIn("both-down", p.stdout)


if __name__ == "__main__":
    unittest.main()


# ---- AWS call shapes, validated against the botocore service model (no network) ----
try:
    import boto3
    from botocore.stub import Stubber, ANY
except ImportError:  # pragma: no cover
    boto3 = None


@unittest.skipIf(boto3 is None, "boto3 not installed")
class AwsShapes(unittest.TestCase):
    def session(self):
        return boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="us-east-1")

    def test_set_health_publishes_high_resolution_metrics_per_target(self):
        be = ft.AwsBackend(self.session(), "us-east-1", "Z1")
        with Stubber(be.cw) as st:
            def md(role, value):
                return {"MetricName": "Unhealthy", "Dimensions": [{"Name": "Target", "Value": role}],
                        "Timestamp": ANY, "Value": value, "Unit": "None", "StorageResolution": 1}
            st.add_response("put_metric_data", {}, {"Namespace": "AmalDnsTest",
                                                    "MetricData": [md("primary", 1.0), md("secondary", 0.0)]})
            be.set_health(False, True)
            st.assert_no_pending_responses()

    def test_answer_uses_test_dns_answer_for_the_test_name_only(self):
        be = ft.AwsBackend(self.session(), "us-east-1", "Z1")
        ok = {"Nameserver": "ns-1.awsdns-00.com", "RecordName": ft.TEST_NAME, "RecordType": "A",
              "RecordData": ["192.0.2.10"], "ResponseCode": "NOERROR", "Protocol": "UDP"}
        with Stubber(be.r53) as st:
            st.add_response("test_dns_answer", ok, {"HostedZoneId": "Z1", "RecordName": ft.TEST_NAME, "RecordType": "A"})
            self.assertEqual(be.answer(), (["192.0.2.10"], "ns-1.awsdns-00.com"))
            st.add_response("test_dns_answer", dict(ok, ResponseCode="SERVFAIL", RecordData=[]))
            with self.assertRaises(RuntimeError):
                be.answer()

    def test_discover_with_a_correct_test_pair(self):
        zone, recs, hcs, alarms = good_topology()
        r53 = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="us-east-1").client("route53")
        cw = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="us-east-1").client("cloudwatch")

        class S:
            def client(self, name, region_name=None):
                return {"route53": r53, "cloudwatch": cw}[name]

        with Stubber(r53) as s53, Stubber(cw) as scw:
            s53.add_response("list_hosted_zones_by_name", {
                "HostedZones": [{"Id": "/hostedzone/ZEB", "Name": zone, "CallerReference": "c", "Config": {"PrivateZone": False}}],
                "DNSName": ft.ZONE_NAME, "IsTruncated": False, "MaxItems": "5"})
            s53.add_response("get_hosted_zone", {"HostedZone": {"Id": "/hostedzone/ZEB", "Name": zone, "CallerReference": "c"},
                                                 "DelegationSet": {"NameServers": ["ns-1.awsdns-00.com"]}})
            s53.add_response("list_resource_record_sets", {
                "ResourceRecordSets": recs, "IsTruncated": False, "MaxItems": "10"},
                {"HostedZoneId": "ZEB", "StartRecordName": ft.TEST_NAME, "StartRecordType": "A", "MaxItems": "10"})
            for hc_id in ("hc-p", "hc-s"):
                s53.add_response("get_health_check", {"HealthCheck": {
                    "Id": hc_id, "CallerReference": "c", "HealthCheckConfig": hcs[hc_id]["HealthCheckConfig"],
                    "HealthCheckVersion": 1}},
                    {"HealthCheckId": hc_id})
            scw.add_response("describe_alarms", {"MetricAlarms": [
                dict(a) for a in alarms.values()]},
                {"AlarmNames": ["amal-dns-failover-test-primary", "amal-dns-failover-test-secondary"]})
            zone_id, topo = ft.discover(S(), "us-east-1", None, "amal-dns")
        self.assertEqual(zone_id, "ZEB")
        self.assertEqual(topo["secondary"]["ip"], "192.0.2.20")
