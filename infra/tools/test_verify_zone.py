"""Unit tests for verify_zone.py (no AWS, no network). Run: python -m unittest discover -s infra/tools"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_zone as vz  # noqa: E402

NS = ["ns-1.awsdns-00.com", "ns-2.awsdns-01.net", "ns-3.awsdns-02.org", "ns-4.awsdns-03.co.uk"]
INV = {
    "source": "test",
    "records": [
        {"name": "@", "type": "MX", "ttl": 60, "values": ["5 amalbank-so.mail.protection.outlook.com."]},
        {"name": "@", "type": "TXT", "ttl": 300, "values": ['"v=spf1 include:spf.protection.outlook.com -all"']},
        {"name": "autodiscover", "type": "TXT", "ttl": 300, "values": ['"autodiscover.outlook.com"']},
    ],
    "_conversion": {"excluded_exceptions": [
        {"name": "@", "type": "A", "ttl": 60, "values": ["34.198.182.201"]},
        {"name": "www", "type": "A", "ttl": 60, "values": ["34.198.182.201"]}]},
}


def rrset(name, rtype, ttl, values, alias=None):
    r = {"Name": name, "Type": rtype}
    if alias:
        r["AliasTarget"] = {"DNSName": alias, "HostedZoneId": "Z2FDTNDATAQYW2", "EvaluateTargetHealth": False}
    else:
        r["TTL"] = ttl
        r["ResourceRecords"] = [{"Value": v} for v in values]
    return r


def good_zone(web="legacy"):
    rs = [
        rrset("amalbank.so.", "NS", 900, [n + "." for n in NS]),
        rrset("amalbank.so.", "SOA", 900, [NS[0] + ". awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400"]),
        rrset("amalbank.so.", "MX", 60, ["5 amalbank-so.mail.protection.outlook.com."]),
        rrset("amalbank.so.", "TXT", 300, ['"v=spf1 include:spf.protection.outlook.com -all"']),
        rrset("autodiscover.amalbank.so.", "TXT", 300, ['"autodiscover.outlook.com"']),
    ]
    if web == "legacy":
        rs += [rrset("amalbank.so.", "A", 60, ["34.198.182.201"]), rrset("www.amalbank.so.", "A", 60, ["34.198.182.201"])]
    else:
        for n in ("amalbank.so.", "www.amalbank.so."):
            for t in ("A", "AAAA"):
                rs.append(rrset(n, t, None, [], alias="d111.cloudfront.net."))
    return rs


class Normalising(unittest.TestCase):
    def test_fqdn(self):
        self.assertEqual(vz.fqdn("@"), "amalbank.so.")
        self.assertEqual(vz.fqdn("WWW"), "www.amalbank.so.")
        self.assertEqual(vz.fqdn("*"), "*.amalbank.so.")

    def test_value_normalisation(self):
        self.assertEqual(vz.norm_value("MX", "5 Mail.Example.COM."), "5 mail.example.com.")
        self.assertEqual(vz.norm_value("CNAME", "Target.EXAMPLE.com."), "target.example.com.")
        self.assertEqual(vz.norm_value("TXT", '"Keep CASE"'), '"Keep CASE"')

    def test_wildcard_escape_in_route53_names(self):
        m = vz.rrsets_to_map([rrset("\\052.amalbank.so.", "A", 60, ["192.0.2.1"])])
        self.assertIn(("*.amalbank.so.", "A"), m)


class Expected(unittest.TestCase):
    def test_legacy_adds_apex_and_www_a_from_exceptions(self):
        e = vz.expected_records(INV, "legacy")
        self.assertEqual(e[("amalbank.so.", "A")], {"ttl": 60, "values": ["34.198.182.201"], "alias": False})
        self.assertIn(("www.amalbank.so.", "A"), e)
        self.assertEqual(len(e), 5)

    def test_cloudfront_expects_aliases(self):
        e = vz.expected_records(INV, "cloudfront")
        self.assertTrue(e[("www.amalbank.so.", "AAAA")]["alias"])
        self.assertEqual(len(e), 3 + 4)

    def test_wildcard_replaces_www(self):
        e = vz.expected_records(INV, "cloudfront", wildcard=True)
        self.assertIn(("*.amalbank.so.", "A"), e)
        self.assertNotIn(("www.amalbank.so.", "A"), e)


class ZoneContents(unittest.TestCase):
    def run_checks(self, rrsets, web="legacy"):
        return vz.check_zone_contents(vz.expected_records(INV, web), vz.rrsets_to_map(rrsets), 900, NS)

    def fails(self, checks):
        return [c for c in checks if c.status == "FAIL"]

    def test_good_legacy_zone_has_no_failures(self):
        self.assertEqual(self.fails(self.run_checks(good_zone())), [])

    def test_good_cloudfront_zone_has_no_failures(self):
        self.assertEqual(self.fails(self.run_checks(good_zone("cloudfront"), "cloudfront")), [])

    def test_missing_record_fails(self):
        z = [r for r in good_zone() if not (r["Type"] == "MX")]
        self.assertTrue(any("MX" in c.name and "missing" in c.detail for c in self.fails(self.run_checks(z))))

    def test_wrong_value_and_ttl_fail(self):
        z = good_zone()
        z[2] = rrset("amalbank.so.", "MX", 3600, ["0 amalbank-so.mail.protection.outlook.com."])
        d = " ".join(c.detail for c in self.fails(self.run_checks(z)))
        self.assertIn("values", d)
        self.assertIn("ttl 3600", d)

    def test_unexpected_record_fails(self):
        z = good_zone() + [rrset("extra.amalbank.so.", "A", 60, ["192.0.2.9"])]
        self.assertTrue(any("unexpected" in c.detail for c in self.fails(self.run_checks(z))))

    def test_acm_validation_cname_is_info_not_failure(self):
        z = good_zone() + [rrset("_abc.amalbank.so.", "CNAME", 300, ["_def.acm-validations.aws."])]
        checks = self.run_checks(z)
        self.assertEqual(self.fails(checks), [])
        self.assertTrue(any(c.status == "INFO" and "ACM" in c.detail for c in checks))

    def test_long_ns_ttl_fails(self):
        z = good_zone()
        z[0] = rrset("amalbank.so.", "NS", 172800, [n + "." for n in NS])
        self.assertTrue(any(c.name == "NS TTL" for c in self.fails(self.run_checks(z))))

    def test_wrong_ns_set_fails(self):
        z = good_zone()
        z[0] = rrset("amalbank.so.", "NS", 900, ["ns1.no-ip.com."])
        self.assertTrue(any(c.name == "NS values" for c in self.fails(self.run_checks(z))))

    def test_alias_where_plain_expected_fails(self):
        z = [r for r in good_zone() if not (r["Name"] == "www.amalbank.so." and r["Type"] == "A")]
        z.append(rrset("www.amalbank.so.", "A", None, [], alias="d111.cloudfront.net."))
        self.assertTrue(any("alias" in c.detail for c in self.fails(self.run_checks(z))))


class FakeAws:
    def __init__(self, rrsets, wildcard=False):
        self.rr = rrsets
        self.wildcard = wildcard

    def rrsets(self, zid):
        return self.rr

    def test_answer(self, zid, name, rtype):
        m = vz.rrsets_to_map(self.rr)
        key = (name.lower().rstrip(".") + ".", rtype)
        if key in m and not m[key]["alias"]:
            return "NOERROR", m[key]["values"]
        return ("NOERROR" if self.wildcard else "NXDOMAIN"), []


class Pre(unittest.TestCase):
    def test_pre_passes_on_good_zone(self):
        checks = vz.run_pre(FakeAws(good_zone()), "Z1", NS, vz.expected_records(INV, "legacy"), 900, False)
        self.assertEqual([c for c in checks if c.status == "FAIL"], [])
        self.assertTrue(any(c.name == "unknown name behaviour" and c.status == "PASS" for c in checks))

    def test_pre_flags_wildcard_where_none_expected(self):
        checks = vz.run_pre(FakeAws(good_zone(), wildcard=True), "Z1", NS, vz.expected_records(INV, "legacy"), 900, False)
        self.assertTrue(any(c.name == "unknown name behaviour" and c.status == "FAIL" for c in checks))


class Post(unittest.TestCase):
    def make_query(self, ns_per_resolver, answers=None):
        answers = answers or {}

        def q(ip, name, rtype, timeout=5):
            if rtype == "NS":
                return "NOERROR", ns_per_resolver[ip]
            key = (name.lower().rstrip(".") + ".", rtype)
            return answers.get(key, ("NXDOMAIN", []))
        return q

    def answers_for_legacy(self):
        return {k: ("NOERROR", v["values"]) for k, v in vz.expected_records(INV, "legacy").items()}

    def test_delegation_pending_then_pass_with_wait(self):
        res = {"a": "1.1.1.1"}
        state = {"n": 0}
        ans = self.answers_for_legacy()

        def q(ip, name, rtype, timeout=5):
            if rtype == "NS":
                state["n"] += 1
                return "NOERROR", (["ns1.no-ip.com."] if state["n"] < 3 else [n + "." for n in NS])
            return ans.get((name.lower().rstrip(".") + ".", rtype), ("NXDOMAIN", []))

        t = {"now": 0.0}
        checks = vz.run_post(None, "Z1", NS, vz.expected_records(INV, "legacy"), 600, res, query=q,
                             sleep=lambda s: t.__setitem__("now", t["now"] + s), now=lambda: t["now"])
        self.assertEqual([c for c in checks if c.status in ("FAIL", "PENDING")], [])
        self.assertEqual(state["n"], 3)

    def test_delegation_never_arrives_is_pending_not_fail(self):
        res = {"a": "1.1.1.1"}
        q = self.make_query({"1.1.1.1": ["ns1.no-ip.com."]}, self.answers_for_legacy())
        checks = vz.run_post(None, "Z1", NS, vz.expected_records(INV, "legacy"), 0, res, query=q, sleep=lambda s: None)
        self.assertTrue(any(c.area == "delegation" and c.status == "PENDING" for c in checks))
        self.assertFalse([c for c in checks if c.status == "FAIL"])

    def test_wrong_public_answer_fails(self):
        ans = self.answers_for_legacy()
        ans[("amalbank.so.", "MX")] = ("NOERROR", ["0 other.example.com."])
        q = self.make_query({"1.1.1.1": [n + "." for n in NS]}, ans)
        checks = vz.run_post(None, "Z1", NS, vz.expected_records(INV, "legacy"), 0, {"a": "1.1.1.1"}, query=q)
        self.assertTrue(any(c.status == "FAIL" and "amalbank.so. MX" in c.name for c in checks))

    def test_resolver_error_fails(self):
        def q(ip, name, rtype, timeout=5):
            raise TimeoutError()
        checks = vz.run_post(None, "Z1", NS, vz.expected_records(INV, "legacy"), 0, {"a": "1.1.1.1"}, query=q)
        self.assertTrue(any(c.status == "FAIL" for c in checks))


class CloudState(unittest.TestCase):
    class Acm:
        def __init__(self, status): self.status = status
        def list_certificates(self): return {"CertificateSummaryList": [{"DomainName": "amalbank.so", "CertificateArn": "arn:x"}]}
        def describe_certificate(self, CertificateArn): return {"Certificate": {"DomainName": "amalbank.so", "Status": self.status, "SubjectAlternativeNames": ["amalbank.so", "www.amalbank.so"]}}

    class Cf:
        def __init__(self, items): self.items = items
        def list_distributions(self): return {"DistributionList": {"Items": self.items}} if self.items else {}

    class Aws:
        def __init__(self, acm, cf): self.acm, self.cf = acm, cf

    def test_pending_certificate(self):
        c = vz.cloud_state(self.Aws(self.Acm("PENDING_VALIDATION"), self.Cf([])))
        self.assertEqual([x.status for x in c], ["PENDING", "INFO"])

    def test_issued_and_deployed(self):
        d = [{"Id": "E1", "DomainName": "d1.cloudfront.net", "Status": "Deployed", "Aliases": {"Items": ["amalbank.so", "www.amalbank.so"]}}]
        c = vz.cloud_state(self.Aws(self.Acm("ISSUED"), self.Cf(d)))
        self.assertEqual([x.status for x in c], ["PASS", "PASS"])

    def test_failed_certificate(self):
        c = vz.cloud_state(self.Aws(self.Acm("FAILED"), self.Cf([])))
        self.assertEqual(c[0].status, "FAIL")


class Evidence(unittest.TestCase):
    def test_files_written(self):
        checks = [vz.Check("records", "x", "PASS"), vz.Check("delegation", "y", "PENDING", "a|b")]
        with tempfile.TemporaryDirectory() as d:
            base, counts = vz.write_evidence(d, "pre", checks, {"zone_id": "Z1", "account": "1"})
            with open(base + ".md") as fh:
                md = fh.read()
            with open(base + ".json") as fh:
                self.assertEqual(len(json.load(fh)["checks"]), 2)
        self.assertEqual(counts["PENDING"], 1)
        self.assertIn("a/b", md)  # pipes are escaped so the Markdown table stays intact


class Cli(unittest.TestCase):
    def test_help_runs(self):
        p = subprocess.run([sys.executable, os.path.join(HERE, "verify_zone.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("pre", p.stdout)

    def test_real_inventory_parses_into_expected_records(self):
        with open(vz.DEFAULT_INVENTORY) as fh:
            inv = json.load(fh)
        e = vz.expected_records(inv, "legacy")
        self.assertIn(("amalbank.so.", "MX"), e)
        self.assertEqual(e[("www.amalbank.so.", "A")]["values"], ["34.198.182.201"])


if __name__ == "__main__":
    unittest.main()
