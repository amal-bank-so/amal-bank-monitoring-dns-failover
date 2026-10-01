"""Guards the split into independent stacks. Run: python -m unittest discover -s infra/tools"""
import glob
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INFRA = os.path.dirname(HERE)
STACKS = os.path.join(INFRA, "stacks")
NAMES = ["shared", "amalbank-so", "ebanking"]
sys.path.insert(0, HERE)


def read(*parts):
    with open(os.path.join(*parts)) as fh:
        return fh.read()


def strip_comments(text):
    out = []
    for line in text.splitlines():
        out.append(re.sub(r'(^|\s)#.*$', "", line))
    return "\n".join(out)


def tf_text(stack):
    """All Terraform code of a stack, comments removed (so prose can mention other stacks)."""
    return "\n".join(strip_comments(read(f)) for f in sorted(glob.glob(os.path.join(STACKS, stack, "*.tf"))))


def assert_absent(test, banned, text, stack):
    for b in banned:
        test.assertNotIn(b, text, f"stack {stack!r} must not contain {b!r}")


def default_of(stack, var):
    m = re.search(r'variable "%s" \{.*?default\s*=\s*"([^"]*)"' % var, read(STACKS, stack, "variables.tf"), re.S)
    return m.group(1) if m else None


class Layout(unittest.TestCase):
    def test_exactly_the_expected_stacks_exist(self):
        self.assertEqual(sorted(d for d in os.listdir(STACKS) if os.path.isdir(os.path.join(STACKS, d))), sorted(NAMES))

    def test_common_files_are_identical_across_stacks(self):
        for f in ("providers.tf", "versions.tf", ".terraform.lock.hcl"):
            first = read(STACKS, NAMES[0], f)
            for st in NAMES[1:]:
                self.assertEqual(read(STACKS, st, f), first, f"{st}/{f} differs from {NAMES[0]}/{f}")

    def test_each_stack_has_its_own_state_key(self):
        keys = []
        for st in NAMES:
            m = re.search(r'^key\s*=\s*"([^"]+)"', read(STACKS, st, "backend.hcl.example"), re.M)
            self.assertEqual(m.group(1), f"{st}/terraform.tfstate")
            keys.append(m.group(1))
        self.assertEqual(len(set(keys)), len(keys))

    def test_stack_name_local_matches_directory(self):
        for st in NAMES:
            self.assertIn(f'stack_name = "{st}"', read(STACKS, st, "locals.tf"))

    def test_common_variable_defaults_agree(self):
        for var in ("aws_region", "expected_account_id", "name_prefix"):
            vals = {st: default_of(st, var) for st in NAMES}
            self.assertEqual(len(set(vals.values())), 1, vals)

    def test_defaults_agree_with_bootstrap_and_driver(self):
        import failover_test as ft
        self.assertEqual(default_of("shared", "expected_account_id"), ft.EXPECTED_ACCOUNT)
        self.assertIn(f"amal-dns-tfstate-{ft.EXPECTED_ACCOUNT}", read(STACKS, "shared", "backend.hcl.example"))
        self.assertIn(ft.EXPECTED_ACCOUNT, read(INFRA, "bootstrap", "main.tf"))
        self.assertEqual(default_of("shared", "name_prefix"), "amal-dns")  # failover_test.py --name-prefix default

    def test_zone_name_constants_are_exact(self):
        self.assertIn('amalbank_zone = "amalbank.so"', read(STACKS, "amalbank-so", "locals.tf"))
        self.assertIn('ebanking_zone = "ebanking.amalbankso.com"', read(STACKS, "ebanking", "locals.tf"))


class Independence(unittest.TestCase):
    def test_shared_has_no_dns_or_edge_resources(self):
        assert_absent(self, ("aws_route53", "aws_cloudfront", "aws_acm"), tf_text("shared"), "shared")

    def test_amalbank_so_does_not_touch_ebanking(self):
        assert_absent(self, ("ebanking_zone", "aws_route53_health_check", "amalbankso.com", "aws_route53_zone.ebanking"),
                      tf_text("amalbank-so"), "amalbank-so")

    def test_ebanking_does_not_touch_amalbank_so(self):
        assert_absent(self, ("amalbank_zone", "aws_route53_zone.amalbank_so", "aws_cloudfront", "aws_acm"),
                      tf_text("ebanking"), "ebanking")

    def test_each_stack_owns_exactly_the_zones_it_should(self):
        zones = {st: re.findall(r'resource "aws_route53_zone" "(\w+)"', tf_text(st)) for st in NAMES}
        self.assertEqual(zones, {"shared": [], "amalbank-so": ["amalbank_so"], "ebanking": ["ebanking"]})

    def test_alert_topic_is_only_created_in_shared_and_found_by_name(self):
        for st in ("amalbank-so", "ebanking"):
            t = tf_text(st)
            self.assertNotIn('resource "aws_sns_topic"', t, st)
            self.assertIn('name  = "${var.name_prefix}-alerts"', t, st)
        self.assertIn('name = "${var.name_prefix}-alerts"', tf_text("shared"))

    def test_query_log_policies_are_per_stack_and_distinct(self):
        names = [re.search(r'policy_name\s*=\s*"([^"]+)"', read(STACKS, st, "logging.tf")).group(1) for st in ("amalbank-so", "ebanking")]
        self.assertEqual(len(set(names)), 2)

    def test_no_stack_reads_another_stacks_state(self):
        for st in NAMES:
            self.assertNotIn("terraform_remote_state", tf_text(st), st)


if __name__ == "__main__":
    unittest.main()
