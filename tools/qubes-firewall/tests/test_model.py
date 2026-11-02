"""model: rows, the rule editor's fields, the working copy, the save check."""

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_firewall import model  # noqa: E402
from qubes_firewall.rules import Rule, RuleList  # noqa: E402

NOW = 1_800_000_000
DNS = ("10.139.1.1", "10.139.1.2")


def firewall(rules, netvm="sys-firewall", klass="AppVM"):
    return model.QubeFirewall("work", klass, netvm, RuleList(rules),
                              DNS if netvm else ())


class SummaryTest(unittest.TestCase):
    def test_netvm_and_count(self):
        self.assertEqual(model.summary(firewall(["action=accept"])),
                         "net qube sys-firewall · 1 rule")

    def test_no_netvm(self):
        self.assertEqual(model.summary(firewall([], netvm=None)), "no net qube · 0 rules")

    def test_dom0(self):
        self.assertEqual(model.summary(firewall([], None, model.ADMIN_VM)), model.ADMIN_NOTE)


class RowTest(unittest.TestCase):
    def cells(self, line):
        return model.rule_cells(3, Rule(line), now=NOW)

    def test_accept_all(self):
        self.assertEqual(self.cells("action=accept"),
                         ("3", "Allow", "any", "any", "", "", ""))

    def test_full_rule(self):
        self.assertEqual(
            self.cells("action=drop proto=tcp dst4=10.0.0.0/8 dstports=80-443 comment=no web"),
            ("3", "Block", "10.0.0.0/8", "TCP", "80-443", "", "no web"))

    def test_single_port_and_icmp_type(self):
        self.assertEqual(self.cells("action=accept proto=udp dstports=53-53")[4], "53")
        self.assertEqual(self.cells("action=accept proto=icmp icmptype=8")[4], "type 8")

    def test_dns_and_any_host_prefix(self):
        self.assertEqual(self.cells("action=accept specialtarget=dns")[2], "DNS servers")
        self.assertEqual(model.destination_text(Rule(None, action="accept", dsthost="0.0.0.0/0")),
                         "any")

    def test_expire(self):
        self.assertEqual(self.cells(f"action=accept expire={NOW - 1}")[5], "expired")
        local = datetime.datetime.fromtimestamp(NOW + 3600).strftime(model.EXPIRE_FORMAT)
        self.assertEqual(self.cells(f"action=accept expire={NOW + 3600}")[5], local)


class FormTest(unittest.TestCase):
    def test_new_rule_defaults_to_accept_all(self):
        rule, problems = model.rule_from_form(model.form_from_rule(None))
        self.assertEqual(problems, [])
        self.assertEqual(rule.rule, "action=accept")

    def test_round_trip(self):
        for line in ("action=accept proto=tcp dsthost=example.com dstports=443-443 comment=web",
                     "action=drop proto=icmp icmptype=8",
                     "action=accept specialtarget=dns",
                     "action=drop dst6=2001:db8::/32",
                     f"action=accept proto=udp dstports=1000-2000 expire={NOW + 600}"):
            with self.subTest(line=line):
                rule, problems = model.rule_from_form(model.form_from_rule(Rule(line)), now=NOW)
                self.assertEqual(problems, [])
                # The form keeps minutes, so an expiry rounds to the minute.
                self.assertEqual(rule.rule.split(" expire=")[0], line.split(" expire=")[0])

    def test_disabled_fields_are_left_out(self):
        form = model.RuleForm(proto="icmp", dstports="443", icmptype="0")
        rule, problems = model.rule_from_form(form)
        self.assertEqual(problems, [])
        self.assertEqual(rule.rule, "action=accept proto=icmp icmptype=0")
        form = model.RuleForm(proto="", dstports="443")
        self.assertEqual(model.rule_from_form(form)[0].rule, "action=accept")

    def test_engine_rules_are_enforced(self):
        cases = [
            (model.RuleForm(proto="tcp", dstports="0"), "Ports"),
            (model.RuleForm(proto="tcp", dstports="70000"), "Ports out of range"),
            (model.RuleForm(dsthost="Example.com"), "Destination"),
            (model.RuleForm(dsthost="10.0.0.0/33"), "Destination"),
            (model.RuleForm(proto="icmp", icmptype="300"), "ICMP type"),
            (model.RuleForm(comment="ünicode"), "Comment"),
            (model.RuleForm(expire="tomorrow"), "Expires"),
        ]
        for form, expected in cases:
            with self.subTest(form=form):
                rule, problems = model.rule_from_form(form)
                self.assertIsNone(rule)
                self.assertTrue(problems[0].startswith(expected), problems)

    def test_no_doubled_field_name(self):
        _rule, problems = model.rule_from_form(model.RuleForm(proto="tcp", dstports="70000"))
        self.assertEqual(problems, ["Ports out of range"])

    def test_parse_expire(self):
        self.assertIsNone(model.parse_expire(" "))
        self.assertEqual(model.parse_expire("+30", NOW), str(NOW + 30))
        self.assertEqual(model.parse_expire("+30m", NOW), str(NOW + 1800))
        self.assertEqual(model.parse_expire("+2h", NOW), str(NOW + 7200))
        self.assertEqual(model.parse_expire("+1d", NOW), str(NOW + 86400))
        text = datetime.datetime.fromtimestamp(NOW).strftime(model.EXPIRE_FORMAT)
        self.assertEqual(model.parse_expire(text), str(NOW - NOW % 60))
        with self.assertRaises(ValueError):
            model.parse_expire("+5y")

    def test_field_enablement(self):
        self.assertTrue(model.ports_enabled(model.RuleForm(proto="udp")))
        self.assertFalse(model.ports_enabled(model.RuleForm(proto="icmp")))
        self.assertTrue(model.icmptype_enabled(model.RuleForm(proto="icmp")))
        self.assertFalse(model.icmptype_enabled(model.RuleForm()))


class EditSessionTest(unittest.TestCase):
    def setUp(self):
        self.session = model.EditSession(RuleList(["action=accept proto=icmp",
                                                   "action=drop"]))

    def lines(self):
        return [r.rule for r in self.session.rules]

    def test_starts_clean_and_original_is_a_copy(self):
        self.assertFalse(self.session.dirty)
        self.session.rules.append("action=accept")
        self.assertTrue(self.session.dirty)
        self.assertEqual(len(self.session.original), 2)

    def test_add_below_selection_or_at_end(self):
        self.assertEqual(self.session.add(Rule("action=accept"), after=0), 1)
        self.assertEqual(self.lines()[1], "action=accept")
        self.assertEqual(self.session.add(Rule("action=accept proto=tcp")), 3)

    def test_replace_keeps_position(self):
        self.assertEqual(self.session.replace(0, Rule("action=accept proto=udp")), 0)
        self.assertEqual(self.lines(), ["action=accept proto=udp", "action=drop"])

    def test_delete_selects_neighbour(self):
        self.assertEqual(self.session.delete(1), 0)
        self.assertIsNone(self.session.delete(0))

    def test_move(self):
        self.assertEqual(self.session.move_down(0), 1)
        self.assertEqual(self.lines(), ["action=drop", "action=accept proto=icmp"])
        self.assertEqual(self.session.move_up(1), 0)
        self.assertFalse(self.session.dirty)


class CheckSaveTest(unittest.TestCase):
    def check(self, rules, **kw):
        return model.check_save(firewall(rules, **kw), RuleList(rules), now=NOW)

    def test_plain_lists_have_no_warnings(self):
        self.assertEqual(self.check(["action=accept"]), ([], []))
        self.assertEqual(self.check([]), ([], []))

    def test_allow_then_block_the_rest_is_not_a_lockout(self):
        # qube Settings' "limit outgoing" shape.
        self.assertEqual(self.check(["action=accept specialtarget=dns",
                                     "action=accept dsthost=example.com",
                                     "action=drop comment=rest"]), ([], []))

    def test_lone_block_all_is_a_lockout(self):
        _problems, warnings = self.check(["action=drop"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("locks the qube out", warnings[0])

    def test_block_all_first_is_still_reported(self):
        _problems, warnings = self.check(["action=drop", "action=accept"])
        self.assertTrue(any("locks the qube out" in w for w in warnings))

    def test_block_covering_dns_is_reported(self):
        _problems, warnings = self.check(["action=drop dst4=10.0.0.0/8", "action=accept"])
        self.assertTrue(any("10.139.1.1" in w for w in warnings))

    def test_expired_rule_is_reported(self):
        _problems, warnings = self.check([f"action=accept expire={NOW - 10}"])
        self.assertEqual(len(warnings), 1)

    def test_invalid_rule_is_a_problem(self):
        problems, warnings = self.check(["action=accept proto=tcp dstports=0-0"])
        self.assertEqual(warnings, [])
        self.assertTrue(problems[0].startswith("Rule 0:"))


if __name__ == "__main__":
    unittest.main()
