# Copied from qvm-firewall-ui v1.0.0 (tests/test_rules.py), with the package renamed.
# -*- coding: utf-8 -*-
'''Tier-1 tests for qubes_firewall.rules — the core rule model.

Every normalization from DESIGN.md §2 / the work-order card has a named test.
The behaviors are mirrored from upstream qubesadmin.firewall, so tests assert
upstream's exact semantics — including its quirks, which are named as such so
nobody "fixes" a mirror divergence by accident.

The corpus round-trip (TestCorpusRoundTrip) runs against
fixtures/dom0-sample/ when — and only when — the real capture exists. A
hand-written fixture must never stand in for it (DESIGN.md §6: "the corpus is
an oracle, not a fixture"); until collect-oracle.sh has been run in dom0, the
corpus tests skip with an explicit GAP message, which the handoff must state.
'''

import os
import sys
import time
import unittest

# Layout-independent import shim. Running this file directly -- `python3
# tests/test_rules.py` from the repo root, or `python3 test_rules.py` from
# inside tests/ -- puts THIS module's own directory on sys.path, not the repo
# root, so `qubes_firewall` is not importable and the run dies with
# ModuleNotFoundError before a single test executes. That is not hypothetical:
# it is what happened on dom0 on 2026-10-15. Insert the repo root explicitly so
# every invocation works from any cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qubes_firewall.rules import (
    Action, DstHost, DstPorts, Expire, IcmpType, Proto, Rule,
    RuleList, SpecialTarget,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.path.join(REPO_ROOT, 'fixtures', 'dom0-sample')
CORPUS_GAP = (
    'GAP: fixtures/dom0-sample/ holds no captured corpus '
    '(vm-<name>-list-raw.txt files with one Admin-API-form rule per line, '
    'as produced by packaging/collect-oracle.sh — see '
    'docs/oracle-handover.md). The script has not been run in dom0 yet, so '
    'the byte-stability round-trip over REAL rules is not evidenced. This '
    'skip is the stated gap; a hand-written fixture must not replace the '
    'corpus.')


class TestActionDomain(unittest.TestCase):
    '''action ∈ {accept, drop} — firewall.py Action(RuleChoice).'''

    def test_action_accepts_exactly_accept_and_drop(self):
        self.assertEqual(Action('accept').rule, 'action=accept')
        self.assertEqual(Action('drop').rule, 'action=drop')

    def test_action_rejects_everything_else(self):
        for bad in ('ACCEPT', 'Deny', 'reject', '', 'accept drop'):
            with self.assertRaises(ValueError):
                Action(bad)

    def test_rule_without_action_is_invalid(self):
        # Card bullet: "a rule with no action is invalid". Upstream message.
        with self.assertRaisesRegex(ValueError, 'missing action='):
            Rule('proto=tcp dstports=443')
        with self.assertRaisesRegex(ValueError, 'missing action='):
            Rule(None, proto='tcp')
        with self.assertRaisesRegex(ValueError, 'missing action='):
            Rule('')


class TestProtoDomain(unittest.TestCase):
    '''proto ∈ {tcp, udp, icmp} — firewall.py Proto(RuleChoice).'''

    def test_proto_accepts_exactly_tcp_udp_icmp(self):
        for name in ('tcp', 'udp', 'icmp'):
            self.assertEqual(Proto(name).rule, 'proto=' + name)

    def test_proto_rejects_everything_else(self):
        for bad in ('TCP', 'sctp', 'igmp', ''):
            with self.assertRaises(ValueError):
                Proto(bad)


class TestHostNormalization(unittest.TestCase):
    '''Bare IPs gain their full prefix; /0 means "any host" and is omitted.'''

    def test_bare_ipv4_gains_prefix_32(self):
        rule = Rule(None, action='accept', dsthost='192.168.1.5')
        dsthost = rule.dsthost
        assert dsthost is not None
        self.assertEqual(dsthost.type, 'dst4')
        self.assertEqual(str(dsthost), '192.168.1.5/32')
        self.assertEqual(rule.rule, 'action=accept dst4=192.168.1.5/32')

    def test_bare_ipv6_gains_prefix_128(self):
        rule = Rule(None, action='accept', dsthost='fd09::1')
        dsthost = rule.dsthost
        assert dsthost is not None
        self.assertEqual(dsthost.type, 'dst6')
        self.assertEqual(str(dsthost), 'fd09::1/128')
        self.assertEqual(rule.rule, 'action=accept dst6=fd09::1/128')

    def test_zero_prefix_ipv4_omitted_from_serialization(self):
        # Card bullet: 0.0.0.0/0 is OMITTED — "any host" is absence.
        rule = Rule('action=accept dst4=0.0.0.0/0')
        dsthost = rule.dsthost
        self.assertIsNotNone(dsthost)       # parsed and retained...
        assert dsthost is not None
        self.assertIsNone(dsthost.rule)     # ...but has no API form
        self.assertEqual(rule.rule, 'action=accept')

    def test_zero_prefix_ipv6_omitted_from_serialization(self):
        rule = Rule('action=drop dst6=::/0 proto=tcp dstports=1-1024')
        dsthost = rule.dsthost
        assert dsthost is not None
        self.assertIsNone(dsthost.rule)
        self.assertEqual(
            rule.rule, 'action=drop proto=tcp dstports=1-1024')

    def test_bare_zero_address_is_a_host_not_any_host(self):
        # 0.0.0.0 (bare) gains /32 and is NOT omitted: prefixlen is 32.
        rule = Rule('action=accept dsthost=0.0.0.0')
        self.assertEqual(rule.rule, 'action=accept dst4=0.0.0.0/32')

    def test_explicit_prefix_is_preserved_verbatim(self):
        rule = Rule('action=accept dst4=10.0.0.0/8')
        self.assertEqual(rule.rule, 'action=accept dst4=10.0.0.0/8')
        rule = Rule('action=accept dst6=fd00::/8')
        self.assertEqual(rule.rule, 'action=accept dst6=fd00::/8')

    def test_dst4_and_dst6_keywords_are_dsthost_synonyms(self):
        # The serialized keyword is the DETECTED type, not the input keyword.
        self.assertEqual(
            Rule('action=accept dst6=2001:db8::1').rule,
            'action=accept dst6=2001:db8::1/128')
        self.assertEqual(
            Rule('action=accept dst4=1.2.3.4').rule,
            'action=accept dst4=1.2.3.4/32')
        self.assertEqual(
            Rule('action=accept dsthost=1.2.3.4').rule,
            'action=accept dst4=1.2.3.4/32')
        self.assertEqual(
            Rule('action=accept dsthost=example.com').rule,
            'action=accept dsthost=example.com')

    def test_hostname_charset_lowercase_digits_dash_dot_underscore(self):
        DstHost('www.example.com')
        DstHost('ex-ample_1.host')
        # Upstream quirk noted: the charset check is the ONLY hostname
        # validation — '1.2.3.4.5x' is a perfectly valid "hostname" here.
        DstHost('1.2.3.4.5x')
        for bad in ('EXAMPLE.COM', 'ex ample.com', 'host!.com', 'höst.example'):
            with self.assertRaisesRegex(ValueError, 'Invalid hostname'):
                DstHost(bad)

    def test_hostname_cannot_carry_a_prefix(self):
        with self.assertRaisesRegex(ValueError, 'Invalid IP address'):
            DstHost('example.com/24')

    def test_prefix_bounds_are_enforced(self):
        with self.assertRaisesRegex(
                ValueError, 'netmask for IPv4 must be <= 32'):
            DstHost('1.2.3.4/33')
        with self.assertRaisesRegex(
                ValueError, 'netmask for IPv6 must be <= 128'):
            DstHost('fd00::/129')
        with self.assertRaisesRegex(ValueError, 'netmask must be non-negative'):
            DstHost('1.2.3.4/-1')
        with self.assertRaisesRegex(ValueError, 'Too many /'):
            DstHost('1.2.3.4/8/8')

    def test_prefixlen_argument_applies_to_bare_ips(self):
        # Upstream signature: DstHost(value, prefixlen=None).
        self.assertEqual(str(DstHost('1.2.3.4', 24)), '1.2.3.4/24')
        self.assertEqual(str(DstHost('fd00::1', 64)), 'fd00::1/64')
        with self.assertRaisesRegex(
                ValueError, 'netmask for IPv4 must be between 0 and 32'):
            DstHost('1.2.3.4', 33)


class TestSpecialTarget(unittest.TestCase):
    '''specialtarget accepts ONLY dns.'''

    def test_specialtarget_dns_accepted(self):
        rule = Rule('action=accept specialtarget=dns')
        self.assertEqual(rule.rule, 'action=accept specialtarget=dns')

    def test_specialtarget_rejects_anything_but_dns(self):
        for bad in ('smtp', 'DNS', 'dhcp', ''):
            with self.assertRaises(ValueError):
                SpecialTarget(bad)
        with self.assertRaises(ValueError):
            Rule('action=accept specialtarget=smtp')

    def test_specialtarget_none_raises_upstream_quirk(self):
        # Upstream quirk, mirrored: the setter has no None special case, so
        # clearing specialtarget raises instead of clearing. The UI rebuilds
        # rules rather than setting elements to None.
        rule = Rule('action=accept specialtarget=dns')
        with self.assertRaises(ValueError):
            rule.specialtarget = None


class TestDstPorts(unittest.TestCase):
    '''dstports: a port or a lo-hi range, valid only for tcp/udp.'''

    def test_single_port_and_range_parse(self):
        self.assertEqual(DstPorts('443').range, [443, 443])
        self.assertEqual(DstPorts('1200-1400').range, [1200, 1400])
        self.assertEqual(DstPorts(80).range, [80, 80])

    def test_api_form_is_always_the_range(self):
        # Upstream: Rule.rule serializes dstports as lo-hi even for a single
        # port; pretty_value collapses equal bounds. Byte-stability of the
        # corpus depends on exactly this.
        rule = Rule('action=accept proto=tcp dstports=443')
        self.assertEqual(rule.rule,
                         'action=accept proto=tcp dstports=443-443')
        self.assertEqual(str(rule.dstports), '443')
        dstports = rule.dstports
        assert dstports is not None
        self.assertEqual(dstports.pretty_value, '443')
        rule = Rule('action=accept proto=udp dstports=1200-1400')
        self.assertEqual(rule.rule,
                         'action=accept proto=udp dstports=1200-1400')

    def test_range_bounds_checked(self):
        with self.assertRaisesRegex(ValueError, 'Ports out of range'):
            DstPorts('70000')
        with self.assertRaisesRegex(ValueError, 'Ports out of range'):
            DstPorts('65537-70000')
        # Upstream splits on '-' FIRST: '-5' dies in int('') with the plain
        # int() ValueError, never reaching the range check. Mirrored as-is.
        with self.assertRaises(ValueError):
            DstPorts('-5')
        with self.assertRaisesRegex(ValueError, 'Invalid port range'):
            DstPorts('1400-1200')
        with self.assertRaises(ValueError):
            DstPorts('1-2-3')

    def test_upstream_bound_quirk_0_and_65536_pass_the_client_model(self):
        # Upstream checks `port < 0 or port > 65536`, so the client library
        # accepts 0 and 65536. The engine's stricter 1-65535 acceptance is
        # validate.py's job (card t_4e486699), not this model's.
        self.assertEqual(DstPorts('0').range, [0, 0])
        self.assertEqual(DstPorts('65536').range, [65536, 65536])

    def test_dstports_requires_proto_tcp_or_udp(self):
        # No proto at all:
        with self.assertRaisesRegex(
                ValueError, "dstports valid only for 'tcp' and 'udp'"):
            Rule('action=accept dstports=443')
        # proto=icmp:
        with self.assertRaisesRegex(
                ValueError, "dstports valid only for 'tcp' and 'udp'"):
            Rule('action=accept proto=icmp dstports=443')

    def test_dstports_parsed_regardless_of_input_order(self):
        # proto is applied before dstports because RULE_PARSE_ORDER says so,
        # not because of the input string order.
        rule = Rule('action=accept dstports=443 proto=tcp')
        self.assertEqual(rule.rule,
                         'action=accept proto=tcp dstports=443-443')


class TestProtoDependencyClearing(unittest.TestCase):
    '''Setting proto clears the elements that no longer apply.'''

    def test_setting_proto_icmp_clears_dstports(self):
        rule = Rule('action=accept proto=tcp dstports=443')
        rule.proto = 'icmp'
        self.assertIsNone(rule.dstports)
        self.assertEqual(rule.rule, 'action=accept proto=icmp')

    def test_setting_proto_tcp_clears_icmptype(self):
        rule = Rule('action=accept proto=icmp icmptype=8')
        rule.proto = 'tcp'
        self.assertIsNone(rule.icmptype)
        self.assertEqual(rule.rule, 'action=accept proto=tcp')

    def test_setting_proto_udp_clears_icmptype(self):
        rule = Rule('action=accept proto=icmp icmptype=8')
        rule.proto = 'udp'
        self.assertIsNone(rule.icmptype)
        self.assertEqual(rule.rule, 'action=accept proto=udp')

    def test_setting_proto_none_clears_both(self):
        rule = Rule('action=accept proto=tcp dstports=443')
        rule.proto = None
        self.assertIsNone(rule.dstports)
        self.assertEqual(rule.rule, 'action=accept')

    def test_icmptype_requires_proto_icmp(self):
        with self.assertRaisesRegex(
                ValueError, "icmptype valid only for 'icmp'"):
            Rule('action=accept icmptype=8')
        with self.assertRaisesRegex(
                ValueError, "icmptype valid only for 'icmp'"):
            Rule('action=accept proto=tcp icmptype=8')

    def test_icmptype_range_0_to_255(self):
        rule = Rule('action=accept proto=icmp icmptype=8')
        self.assertEqual(rule.rule, 'action=accept proto=icmp icmptype=8')
        IcmpType(0)
        IcmpType(255)
        for bad in (256, -1):
            with self.assertRaisesRegex(ValueError, 'ICMP type out of range'):
                IcmpType(bad)


class TestExpire(unittest.TestCase):
    '''expire is unix seconds or +seconds relative.'''

    def test_expire_absolute_unix_seconds(self):
        rule = Rule('action=accept expire=1893456000')
        self.assertEqual(rule.rule, 'action=accept expire=1893456000')

    def test_expire_relative_plus_seconds_resolved_to_absolute(self):
        # Deviation #2 (documented): resolution happens in the setter, like
        # the qvm-firewall CLI does before constructing the upstream Rule.
        before = int(time.time())
        rule = Rule(None, action='accept', expire='+300')
        after = int(time.time())
        value = int(str(rule.expire))
        self.assertGreaterEqual(value, before + 300)
        self.assertLessEqual(value, after + 300)
        # The serialized form is absolute, so round-trips are byte-stable.
        self.assertEqual(rule.rule, 'action=accept expire={}'.format(value))
        self.assertEqual(Rule(rule.rule).rule, rule.rule)

    def test_expire_relative_garbage_raises(self):
        with self.assertRaises(ValueError):
            Rule(None, action='accept', expire='+soon')

    def test_expired_property(self):
        self.assertTrue(Expire(int(time.time()) - 10).expired)
        self.assertFalse(Expire(int(time.time()) + 3600).expired)

    def test_expire_pretty_value_is_relative_seconds(self):
        expire = Expire(int(time.time()) + 300)
        self.assertRegex(expire.pretty_value, r'^\+(29\d|300)s$')


class TestCommentParsing(unittest.TestCase):
    '''comment= is split off BEFORE the other elements are parsed.'''

    def test_comment_is_free_text_with_spaces(self):
        rule = Rule('action=accept proto=tcp dstports=22 '
                    'comment=ssh into the vault qube')
        self.assertEqual(str(rule.comment), 'ssh into the vault qube')
        self.assertEqual(
            rule.rule,
            'action=accept proto=tcp dstports=22-22 '
            'comment=ssh into the vault qube')

    def test_comment_may_contain_keyword_looking_text(self):
        # Everything after the first 'comment=' is comment — even text that
        # looks like rule elements, even '=' characters.
        text = ('action=accept comment=proto=tcp dst4=1.2.3.4 '
                'comment=still one comment')
        rule = Rule(text)
        self.assertEqual(str(rule.comment),
                         'proto=tcp dst4=1.2.3.4 comment=still one comment')
        self.assertIsNone(rule.proto)
        self.assertEqual(rule.rule, text)

    def test_comment_mid_string_swallows_the_rest(self):
        # Upstream partition() semantics: elements after comment= are part
        # of the comment, not elements.
        rule = Rule('action=accept comment=hi proto=tcp')
        self.assertEqual(str(rule.comment), 'hi proto=tcp')
        self.assertIsNone(rule.proto)

    def test_empty_comment_is_dropped_upstream_quirk(self):
        # `if comment:` — an empty trailing comment is falsy and vanishes.
        rule = Rule('action=accept comment=')
        self.assertIsNone(rule.comment)
        self.assertEqual(rule.rule, 'action=accept')

    def test_comment_always_serialized_last(self):
        rule = Rule(None, comment='why', action='drop',
                    specialtarget='dns', expire=1893456000)
        self.assertEqual(
            rule.rule,
            'action=drop specialtarget=dns expire=1893456000 comment=why')


class TestRuleParsing(unittest.TestCase):
    '''Whole-rule parse/serialize behavior mirrored from upstream Rule.'''

    def test_serialization_element_order(self):
        rule = Rule(None, action='drop', proto='udp', dsthost='10.0.0.5',
                    dstports='53', specialtarget='dns', expire=1893456000,
                    comment='d ns')
        self.assertEqual(
            rule.rule,
            'action=drop proto=udp dst4=10.0.0.5/32 dstports=53-53 '
            'specialtarget=dns expire=1893456000 comment=d ns')

    def test_unknown_element_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown rule elements'):
            Rule('action=accept srchost=1.2.3.4')

    def test_element_without_equals_rejected(self):
        # Upstream: dict(split('=', 1)) raises ValueError on a bare token.
        with self.assertRaises(ValueError):
            Rule('action=accept tcp')

    def test_duplicate_keys_last_one_wins(self):
        rule = Rule('action=accept action=drop')
        self.assertEqual(rule.rule, 'action=drop')

    def test_multiple_spaces_between_elements_are_collapsed(self):
        rule = Rule('action=accept    proto=tcp   dstports=443')
        self.assertEqual(rule.rule,
                         'action=accept proto=tcp dstports=443-443')

    def test_kwargs_override_parsed_string(self):
        rule = Rule('action=accept proto=tcp', proto='udp', dstports='53')
        self.assertEqual(rule.rule, 'action=accept proto=udp dstports=53-53')

    def test_keyword_construction_without_string(self):
        rule = Rule(None, action='accept')
        self.assertEqual(rule.rule, 'action=accept')

    def test_equality_against_rule_and_string(self):
        rule = Rule('action=accept proto=tcp dstports=443')
        self.assertEqual(rule, Rule('action=accept proto=tcp dstports=443'))
        # Deviation #1 (documented): upstream's `self.rule == str` typo made
        # this always False; comparing serialized forms is what powers
        # delete-by-match on plain API strings.
        self.assertEqual(rule, 'action=accept proto=tcp dstports=443-443')
        self.assertNotEqual(rule, 'action=drop')
        self.assertNotEqual(rule, 42)

    def test_repr_shows_api_form(self):
        self.assertEqual(repr(Rule('action=accept')), "Rule('action=accept')")

    def test_action_cannot_be_cleared_upstream_quirk(self):
        rule = Rule('action=accept')
        with self.assertRaises(ValueError):
            rule.action = None

    def test_expire_cannot_be_cleared_upstream_quirk(self):
        rule = Rule('action=accept expire=1893456000')
        with self.assertRaises(TypeError):
            rule.expire = None


class TestRoundTripStability(unittest.TestCase):
    '''parse → serialize must be byte-stable on API-form rules.'''

    # Every string here is in API form — the exact shape
    # admin.vm.firewall.Get returns and admin.vm.firewall.Set accepts
    # (dstports always lo-hi, comment last, detected dst4/dst6 keywords,
    # no /0 elements). These exercise the model's idempotence; they are NOT
    # a corpus substitute (see TestCorpusRoundTrip).
    API_FORM_SAMPLES = (
        'action=accept',
        'action=drop',
        'action=accept proto=tcp dstports=443-443',
        'action=accept proto=udp dstports=1200-1400',
        'action=accept proto=icmp icmptype=8',
        'action=accept dst4=192.168.0.0/24',
        'action=drop proto=tcp dst4=10.137.0.41/32 dstports=22-22',
        'action=accept dst6=2001:db8::/32',
        'action=accept proto=udp dst6=fd09::1/128 dstports=53-53',
        'action=accept dsthost=example.com',
        'action=accept proto=udp dstports=53-53 specialtarget=dns',
        'action=accept specialtarget=dns',
        'action=drop expire=1893456000 comment=until new years',
        'action=accept proto=tcp dstports=80-80 comment=let http in',
        'action=accept comment=proto=tcp dstports=21-21 comment=tricky',
    )

    def test_parse_serialize_is_byte_stable_on_api_form(self):
        for text in self.API_FORM_SAMPLES:
            with self.subTest(rule=text):
                self.assertEqual(Rule(text).rule, text)

    def test_serialize_parse_serialize_is_byte_stable(self):
        # For ANY rule the model can build: serialize, re-parse, re-serialize
        # — the second serialization equals the first, byte for byte.
        built = [
            Rule(None, action='accept'),
            Rule(None, action='drop', proto='icmp', icmptype=0),
            Rule(None, action='accept', dsthost='0.0.0.0/0'),
            Rule(None, action='accept', dsthost='::/0'),
            Rule(None, action='accept', dsthost='host-name_1.example',
                 proto='tcp', dstports='1-65535'),
            Rule(None, action='drop', specialtarget='dns',
                 expire='+300', comment='short lived'),
        ]
        for rule in built:
            with self.subTest(rule=repr(rule)):
                first = rule.rule
                self.assertEqual(Rule(first).rule, first)


class TestRuleListOrdering(unittest.TestCase):
    '''The ordered list: append, insert-before, deletes, moves.

    Index is evaluation order: zero-based (qvm-firewall list numbers from 0),
    explicit, bounds-checked. No negative or past-the-end indexes — deviation
    #3, stricter than the CLI's list.insert/list.pop passthrough.'''

    def setUp(self):
        self.rules = RuleList([
            'action=accept proto=tcp dstports=443-443',
            'action=accept specialtarget=dns',
            'action=drop',
        ])

    def test_indexes_are_zero_based_evaluation_order(self):
        self.assertEqual(len(self.rules), 3)
        self.assertEqual(self.rules[0].rule,
                         'action=accept proto=tcp dstports=443-443')
        self.assertEqual(self.rules[2].rule, 'action=drop')
        self.assertEqual([r.rule for r in self.rules],
                         [r.rule for r in self.rules.rules])

    def test_append_adds_at_the_end_and_returns_index(self):
        index = self.rules.append('action=accept proto=icmp icmptype=8')
        self.assertEqual(index, 3)
        self.assertEqual(self.rules[3].rule,
                         'action=accept proto=icmp icmptype=8')

    def test_insert_before_puts_rule_at_that_index(self):
        index = self.rules.insert_before(1, 'action=drop dst4=1.2.3.4/32')
        self.assertEqual(index, 1)
        self.assertEqual(self.rules[1].rule, 'action=drop dst4=1.2.3.4/32')
        self.assertEqual(self.rules[2].rule, 'action=accept specialtarget=dns')
        self.assertEqual(len(self.rules), 4)

    def test_insert_before_zero_and_len(self):
        self.rules.insert_before(0, 'action=accept comment=first')
        self.assertEqual(self.rules[0].comment, 'first')
        self.rules.insert_before(len(self.rules), 'action=accept comment=last')
        self.assertEqual(self.rules[len(self.rules) - 1].comment, 'last')

    def test_insert_before_rejects_out_of_range_and_negative(self):
        with self.assertRaises(IndexError):
            self.rules.insert_before(4, 'action=accept')
        with self.assertRaises(IndexError):
            self.rules.insert_before(-1, 'action=accept')

    def test_delete_by_index_returns_removed_rule(self):
        removed = self.rules.delete_by_index(1)
        self.assertEqual(removed.rule, 'action=accept specialtarget=dns')
        self.assertEqual(len(self.rules), 2)
        self.assertEqual(self.rules[1].rule, 'action=drop')

    def test_delete_by_index_rejects_out_of_range_and_negative(self):
        with self.assertRaises(IndexError):
            self.rules.delete_by_index(3)
        with self.assertRaises(IndexError):
            self.rules.delete_by_index(-1)
        empty = RuleList()
        with self.assertRaises(IndexError):
            empty.delete_by_index(0)

    def test_delete_by_match_removes_first_match_only(self):
        rules = RuleList(['action=accept', 'action=drop', 'action=accept'])
        index, removed = rules.delete_by_match('action=accept')
        self.assertEqual(index, 0)
        self.assertEqual(removed.rule, 'action=accept')
        self.assertEqual([r.rule for r in rules],
                         ['action=drop', 'action=accept'])

    def test_delete_by_match_accepts_rule_objects_and_normalizes(self):
        # Match is by API form: 'dstports=443' matches 'dstports=443-443'.
        index, _ = self.rules.delete_by_match(
            Rule('action=accept proto=tcp dstports=443'))
        self.assertEqual(index, 0)

    def test_delete_by_match_raises_valueerror_when_absent(self):
        with self.assertRaisesRegex(ValueError, 'no rule matches'):
            self.rules.delete_by_match('action=accept proto=icmp icmptype=8')

    def test_move_up_swaps_with_predecessor(self):
        new_index = self.rules.move_up(2)
        self.assertEqual(new_index, 1)
        self.assertEqual(self.rules[1].rule, 'action=drop')
        self.assertEqual(self.rules[2].rule, 'action=accept specialtarget=dns')

    def test_move_down_swaps_with_successor(self):
        new_index = self.rules.move_down(0)
        self.assertEqual(new_index, 1)
        self.assertEqual(self.rules[0].rule, 'action=accept specialtarget=dns')
        self.assertEqual(self.rules[1].rule,
                         'action=accept proto=tcp dstports=443-443')

    def test_move_at_edges_is_noop_returning_same_index(self):
        self.assertEqual(self.rules.move_up(0), 0)
        order_before = [r.rule for r in self.rules]
        self.assertEqual([r.rule for r in self.rules], order_before)
        self.assertEqual(self.rules.move_down(2), 2)
        self.assertEqual([r.rule for r in self.rules], order_before)

    def test_move_rejects_out_of_range_and_negative(self):
        with self.assertRaises(IndexError):
            self.rules.move_up(3)
        with self.assertRaises(IndexError):
            self.rules.move_down(-1)

    def test_rules_property_returns_a_copy(self):
        snapshot = self.rules.rules
        snapshot.append(Rule('action=accept'))
        snapshot[0] = Rule('action=drop')
        self.assertEqual(len(self.rules), 3)
        self.assertEqual(self.rules[0].rule,
                         'action=accept proto=tcp dstports=443-443')

    def test_string_elements_are_coerced_junk_is_not(self):
        rules = RuleList()
        rules.append('action=accept')
        rules.insert_before(0, Rule('action=drop'))
        self.assertEqual([r.rule for r in rules],
                         ['action=drop', 'action=accept'])
        with self.assertRaises(TypeError):
            rules.append(42)
        with self.assertRaises(TypeError):
            rules.append(None)


class TestApiTextRoundTrip(unittest.TestCase):
    '''from_api_text / to_api_text mirror load_rules / save_rules payloads.'''

    API_TEXT = (
        'action=accept proto=tcp dstports=443-443\n'
        'action=accept proto=udp dstports=53-53 specialtarget=dns\n'
        'action=drop dst4=10.137.0.41/32 comment=vault netvm\n'
        'action=accept\n'
    )

    def test_to_api_text_of_from_api_text_is_byte_identical(self):
        rules = RuleList.from_api_text(self.API_TEXT)
        self.assertEqual(len(rules), 4)
        self.assertEqual(rules.to_api_text(), self.API_TEXT)

    def test_empty_text_is_an_empty_list(self):
        rules = RuleList.from_api_text('')
        self.assertEqual(len(rules), 0)
        self.assertEqual(rules.to_api_text(), '')

    def test_blank_line_is_a_parse_error_not_skipped(self):
        # Mirrors Firewall.load_rules: every line becomes a Rule, so a blank
        # line raises 'missing action=' rather than being silently dropped.
        with self.assertRaisesRegex(ValueError, 'missing action='):
            RuleList.from_api_text('action=accept\n\naction=drop\n')

    def test_every_line_round_trips_individually(self):
        for line in self.API_TEXT.splitlines():
            with self.subTest(line=line):
                self.assertEqual(Rule(line).rule, line)


class TestCorpusRoundTrip(unittest.TestCase):
    '''Byte-stability over the REAL captured corpus — the oracle.

    Corpus contract (docs/oracle-handover.md, produced by
    packaging/collect-oracle.sh in dom0, pasted back as
    fixtures/dom0-sample/): the files ``vm-<name>-list-raw.txt`` hold the
    output of ``qvm-firewall --raw <vm> list`` — Admin-API-form rule strings,
    one per line, exactly as ``admin.vm.firewall.Get`` returns them. Blank
    lines are tolerated (paste artifacts); everything else must parse and
    re-serialize byte-identically. The sibling captures
    (``vm-<name>-list.txt`` table, ``vm-<name>-rule.xml`` engine XML,
    ``transcript.log``, ``manifest.txt``) are not API-form rule strings and
    are not round-tripped here — the XML is the engine's own representation
    and belongs to the adapter seam, not to this model.

    Until the corpus exists these tests skip with an explicit GAP — the skip
    message is the "stated gap" the card demands, and no hand-written fixture
    may replace the capture.'''

    @staticmethod
    def corpus_files():
        if not os.path.isdir(CORPUS_DIR):
            return []
        found = []
        for root, _dirs, files in os.walk(CORPUS_DIR):
            for name in files:
                if name.startswith('vm-') and name.endswith('-list-raw.txt'):
                    found.append(os.path.join(root, name))
        return sorted(found)

    def test_corpus_has_been_captured(self):
        if not self.corpus_files():
            self.skipTest(CORPUS_GAP)

    def test_parse_serialize_byte_stable_over_corpus(self):
        files = self.corpus_files()
        if not files:
            self.skipTest(CORPUS_GAP)
        total = 0
        for path in files:
            with open(path, encoding='utf-8') as handle:
                for line in handle.read().splitlines():
                    if not line.strip():
                        continue
                    total += 1
                    with self.subTest(file=path, line=line):
                        self.assertEqual(Rule(line).rule, line)
        self.assertGreater(
            total, 0,
            'corpus files exist but contain no rule lines: {!r}'.format(files))

    def test_corpus_round_trips_through_rulelist(self):
        files = self.corpus_files()
        if not files:
            self.skipTest(CORPUS_GAP)
        for path in files:
            with open(path, encoding='utf-8') as handle:
                text = handle.read()
            lines = [line for line in text.splitlines() if line.strip()]
            rebuilt = ''.join('{}\n'.format(line) for line in lines)
            with self.subTest(file=path):
                self.assertEqual(
                    RuleList.from_api_text(rebuilt).to_api_text(), rebuilt)


if __name__ == '__main__':
    unittest.main()
