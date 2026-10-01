"""Unit tests for bind_to_inventory.py. Run: python -m unittest discover -s infra/terraform/tools"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bind_to_inventory as b  # noqa: E402

ZONE = "amalbank.so"

BASIC = """\
$ORIGIN amalbank.so.
$TTL 300
@   IN SOA ns1.no-ip.com. hostmaster.no-ip.com. (
        2025121001 ; serial
        10800 1800 604800 1800 )
@   IN NS  ns1.no-ip.com.
@   IN NS  ns2.no-ip.com.
@   60 IN MX 5 amalbank-so.mail.protection.outlook.com.
@   IN TXT "v=spf1 include:spf.protection.outlook.com -all"
    IN TXT "MS=ms1234; not a comment" "second string"
autodiscover IN CNAME autodiscover.outlook.com.
www IN CNAME @
WWW2 IN A 192.0.2.1
*   IN MX 10 mail
"""


def conv(text, **kw):
    return b.convert(text, ZONE, **kw)


def rec(res, name, rtype):
    matches = [r for r in res.records if r["name"] == name and r["type"] == rtype]
    assert len(matches) == 1, f"expected one {name} {rtype}, got {matches}"
    return matches[0]


class Normalisation(unittest.TestCase):
    def setUp(self):
        self.res = conv(BASIC)

    def test_no_errors_or_warnings(self):
        self.assertEqual(self.res.errors, [])
        self.assertEqual(self.res.warnings, [])
        self.assertTrue(self.res.ok(strict=True))

    def test_apex_is_at_sign_and_ttl_preserved(self):
        mx = rec(self.res, "@", "MX")
        self.assertEqual(mx["ttl"], 60)
        self.assertEqual(mx["values"], ["5 amalbank-so.mail.protection.outlook.com."])

    def test_default_ttl_applies_when_record_has_none(self):
        self.assertEqual(rec(self.res, "autodiscover", "CNAME")["ttl"], 300)

    def test_apex_ns_and_soa_skipped_but_reported(self):
        self.assertFalse([r for r in self.res.records if r["type"] in ("NS", "SOA")])
        kinds = sorted(s["type"] for s in self.res.skipped_apex)
        self.assertEqual(kinds, ["NS", "NS", "SOA"])

    def test_txt_keeps_quotes_semicolons_and_multiple_strings(self):
        txt = rec(self.res, "@", "TXT")
        self.assertEqual(len(txt["values"]), 2)  # blank owner repeated the apex
        self.assertIn('"v=spf1 include:spf.protection.outlook.com -all"', txt["values"])
        self.assertIn('"MS=ms1234; not a comment" "second string"', txt["values"])

    def test_cname_to_at_becomes_absolute_apex(self):
        self.assertEqual(rec(self.res, "www", "CNAME")["values"], ["amalbank.so."])

    def test_relative_target_is_qualified_once(self):
        self.assertEqual(rec(self.res, "*", "MX")["values"], ["10 mail.amalbank.so."])

    def test_external_target_with_trailing_dot_untouched(self):
        self.assertEqual(rec(self.res, "autodiscover", "CNAME")["values"], ["autodiscover.outlook.com."])

    def test_owner_names_are_lowercased_and_wildcard_kept(self):
        names = {r["name"] for r in self.res.records}
        self.assertIn("www2", names)
        self.assertIn("*", names)
        self.assertNotIn("WWW2", names)

    def test_every_value_is_unchanged_by_zone_suffix_doubling(self):
        for r in self.res.records:
            for v in r["values"]:
                self.assertNotIn("amalbank.so.amalbank.so", v)
            self.assertFalse(r["name"].endswith("amalbank.so"))
            self.assertFalse(r["name"].endswith("."))

    def test_records_sorted_apex_first(self):
        self.assertEqual(self.res.records[0]["name"], "@")


class SafetyChecks(unittest.TestCase):
    def test_owner_with_zone_suffix_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nwww.amalbank.so IN A 192.0.2.1\n")
        self.assertTrue(any("trailing dot" in e for e in res.errors), res.errors)
        self.assertEqual(res.records, [])

    def test_bare_zone_name_as_owner_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\namalbank.so IN A 192.0.2.1\n")
        self.assertTrue(res.errors)

    def test_suspicious_target_is_a_warning_and_strict_fails(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nautodiscover IN CNAME autodiscover.outlook.com\n")
        self.assertEqual(rec(res, "autodiscover", "CNAME")["values"], ["autodiscover.outlook.com.amalbank.so."])
        self.assertEqual(len(res.warnings), 1)
        self.assertTrue(res.ok())
        self.assertFalse(res.ok(strict=True))

    def test_legitimate_in_zone_target_not_flagged(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nweb IN CNAME host1.edge\n")
        self.assertEqual(res.warnings, [])

    def test_ttl_conflict_in_one_set_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\n@ 60 IN MX 5 a.example.com.\n@ 3600 IN MX 10 b.example.com.\n")
        self.assertTrue(any("different TTLs" in e for e in res.errors), res.errors)
        self.assertEqual(res.records, [])

    def test_unsupported_type_is_listed_and_fails_unless_allowed(self):
        res = conv('$ORIGIN amalbank.so.\n$TTL 300\nx IN HINFO "cpu" "os"\n')
        self.assertEqual(len(res.unsupported), 1)
        self.assertEqual(res.unsupported[0]["type"], "HINFO")
        self.assertFalse(res.ok())
        self.assertTrue(res.ok(allow_unsupported=True))
        self.assertEqual(res.records, [])

    def test_apex_cname_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\n@ IN CNAME other.example.com.\n")
        self.assertTrue(res.errors)

    def test_out_of_zone_owner_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nfoo.example.com. IN A 192.0.2.1\n")
        self.assertTrue(any("outside the zone" in e for e in res.errors), res.errors)

    def test_cname_with_other_data_is_an_error(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nw IN CNAME t.example.com.\nw IN A 192.0.2.1\n")
        self.assertTrue(any("cannot coexist" in e for e in res.errors), res.errors)

    def test_out_of_zone_origin_directive_is_an_error(self):
        res = conv("$ORIGIN example.org.\n$TTL 300\nx IN A 192.0.2.1\n")
        self.assertTrue(any("outside the zone" in e for e in res.errors), res.errors)

    def test_out_of_zone_record_does_not_hide_in_zone_records(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nfoo.example.com. IN A 192.0.2.1\nok IN A 192.0.2.2\n")
        self.assertEqual([r["name"] for r in res.records], ["ok"])
        self.assertTrue(res.errors)

    def test_missing_ttl_needs_default(self):
        text = "$ORIGIN amalbank.so.\nweb IN A 192.0.2.1\n"
        self.assertTrue(conv(text).errors)
        res = conv(text, default_ttl=900)
        self.assertEqual(rec(res, "web", "A")["ttl"], 900)

    def test_child_delegation_reported_and_occluded_records_warned(self):
        res = conv(
            "$ORIGIN amalbank.so.\n$TTL 300\nsub IN NS ns1.sub.example.net.\nhost.sub IN A 192.0.2.9\n"
        )
        self.assertEqual(res.child_delegations, [{"name": "sub", "value": "ns1.sub.example.net."}])
        self.assertTrue(any("occluded" in w for w in res.warnings))
        self.assertEqual(rec(res, "sub", "NS")["values"], ["ns1.sub.example.net."])

    def test_duplicate_values_collapse(self):
        res = conv("$ORIGIN amalbank.so.\n$TTL 300\nw IN A 192.0.2.1\nw IN A 192.0.2.1\nw IN A 192.0.2.2\n")
        self.assertEqual(rec(res, "w", "A")["values"], ["192.0.2.1", "192.0.2.2"])

    def test_include_directive_is_refused(self):
        res = conv("$ORIGIN amalbank.so.\n$INCLUDE /etc/passwd\n")
        self.assertTrue(res.errors)

    def test_garbage_input_reports_error_not_traceback(self):
        res = conv("this is not a zone file (\n")
        self.assertTrue(res.errors)


class Contract(unittest.TestCase):
    def test_allowed_types_match_terraform(self):
        with open(os.path.join(HERE, "..", "records.tf")) as fh:
            tf = fh.read()
        m = re.search(r"allowed_record_types\s*=\s*\[(.*?)\]", tf, re.S)
        tf_types = re.findall(r'"([A-Z]+)"', m.group(1))
        self.assertEqual(sorted(tf_types), sorted(b.ALLOWED_TYPES))

    def test_inventory_shape_matches_terraform_expectations(self):
        res = conv(BASIC)
        inv = b.build_inventory(res, "src", "2026-10-02T00:00:00Z", "abc")
        self.assertIs(inv["verified"], False)
        self.assertEqual({"verified", "source", "exported_at", "records"} - set(inv), set())
        for r in inv["records"]:
            self.assertEqual(set(r), {"name", "type", "ttl", "values"})
            self.assertIsInstance(r["ttl"], int)
            self.assertTrue(r["values"])


class Cli(unittest.TestCase):
    @staticmethod
    def _load(path):
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def run_cli(self, text, *extra):
        with tempfile.TemporaryDirectory() as d:
            src, out, rep = (os.path.join(d, n) for n in ("z.zone", "inv.json", "rep.json"))
            with open(src, "w") as fh:
                fh.write(text)
            p = subprocess.run(
                [sys.executable, os.path.join(HERE, "bind_to_inventory.py"), "--zone", ZONE,
                 "--input", src, "--output", out, "--report", rep, *extra],
                capture_output=True, text=True,
            )
            inv = self._load(out)
            report = self._load(rep)
            return p, inv, report

    def test_success_writes_unverified_inventory_with_provenance(self):
        p, inv, rep = self.run_cli(BASIC, "--source", "No-IP export", "--exported-at", "2026-10-02T10:00:00Z")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIs(inv["verified"], False)
        self.assertEqual(inv["source"], "No-IP export")
        self.assertEqual(len(inv["_conversion"]["input_sha256"]), 64)
        self.assertEqual(rep["errors"], [])
        self.assertEqual(len(rep["skipped_apex_ns_soa"]), 3)

    def test_errors_exit_2_and_write_no_inventory(self):
        p, inv, rep = self.run_cli("$ORIGIN amalbank.so.\n$TTL 300\nwww.amalbank.so IN A 192.0.2.1\n")
        self.assertEqual(p.returncode, 2)
        self.assertIsNone(inv)
        self.assertTrue(rep["errors"])  # report is still written for diagnosis

    def test_strict_fails_on_warning(self):
        text = "$ORIGIN amalbank.so.\n$TTL 300\nautodiscover IN CNAME autodiscover.outlook.com\n"
        self.assertEqual(self.run_cli(text)[0].returncode, 0)
        self.assertEqual(self.run_cli(text, "--strict")[0].returncode, 2)

    def test_missing_input_exit_1(self):
        p = subprocess.run([sys.executable, os.path.join(HERE, "bind_to_inventory.py"), "--zone", ZONE,
                            "--input", "/nonexistent.zone"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main()
