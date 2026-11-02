# Copied from qvm-firewall-ui v1.0.0 (tests/test_safety.py), with the package renamed.
# -*- coding: utf-8 -*-
'''Tier-1 tests for qubes_firewall.safety — the five lockout/shadowing
detectors.

Acceptance contract (card t_4e486699): every safety detector has a test
that FAILS WHEN THE DETECTOR IS REMOVED. Each detector therefore has a
dedicated test class asserting that its warnings are actually produced
(code, indexes, message content), and the evidence bundle contains
mutation_check.py, which stubs each detector out in a fresh interpreter
and asserts its class fails — the property proven by watching it, not
asserted by prose.

All clocks are injected (NOW): no test depends on wall time, except the one
that says so. Expiry boundary semantics mirror rules.Expire.expired
(strictly-earlier). The "does NOT fire" tests are as load-bearing as the
"fires" tests: they pin the boundary decisions documented in safety.py's
module docstring (service-limited drops, hostname dsthosts offline, the
reset state producing zero warnings, expired rules being inert).
'''

import datetime
import os
import sys
import unittest

# Layout-independent import shim (same contract as tests/test_rules.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qubes_firewall import safety as s
from qubes_firewall.rules import Rule, RuleList

# Fixed clock: 2027-01-15T08:00:00Z.
NOW = 1800000000
PAST = NOW - 3600
FUTURE = NOW + 3600
NETVM4 = '10.137.0.1'
NETVM6 = 'fd09::1'


def codes(warnings):
    return [w.code for w in warnings]


def indexes(warnings):
    return [w.indexes for w in warnings]


class TestNetvmDeniedDetector(unittest.TestCase):
    '''Detector 1 — a rule that denies the qube's own netvm (card bullet
    1). Fires on: bare (zero-selector) drops, and destination-wide drops
    whose dsthost network contains a caller-supplied netvm address.'''

    def test_bare_drop_fires(self):
        warnings = s.detect_netvm_denied([Rule('action=drop')], now=NOW)
        self.assertEqual(codes(warnings), [s.NETVM_DENIED])
        self.assertEqual(indexes(warnings), [(0,)])
        self.assertIn('bare drop', warnings[0].message)
        self.assertIn('action=drop', warnings[0].message)

    def test_fires_at_the_right_index_in_a_longer_list(self):
        rules = [Rule('action=accept proto=tcp dstports=443'),
                 Rule('action=accept'),
                 Rule('action=drop')]
        warnings = s.detect_netvm_denied(rules, now=NOW)
        # index 1 is accept-all (not this detector's business), index 2 is
        # the bare drop.
        self.assertEqual(indexes(warnings), [(2,)])

    def test_comment_and_future_expire_are_not_selectors(self):
        # expire/comment never narrow what a rule matches (safety.py
        # module docstring): the drop below is still bare.
        rule = Rule(None, action='drop', comment='block everything',
                    expire=str(FUTURE))
        warnings = s.detect_netvm_denied([rule], now=NOW)
        self.assertEqual(codes(warnings), [s.NETVM_DENIED])

    def test_zero_prefix_normalizes_to_bare_drop(self):
        # rules.py mirrors the client model: dst4=0.0.0.0/0 serializes with
        # NO dsthost ("any host is expressed by absence"), so the engine
        # sees a bare drop — and the detector must see it the same way.
        for spec in ('0.0.0.0/0', '::/0'):
            rule = Rule(None, action='drop', dsthost=spec)
            warnings = s.detect_netvm_denied([rule], now=NOW)
            self.assertEqual(codes(warnings), [s.NETVM_DENIED], spec)
            self.assertIn('bare drop', warnings[0].message)

    def test_expired_bare_drop_does_not_fire(self):
        # The engine skips expired rules (qubes/firewall.py qdb_entries /
        # save): an expired lockout is not a lockout. Detector 4 reports it.
        rule = Rule(None, action='drop', expire=str(PAST))
        self.assertEqual(s.detect_netvm_denied([rule], now=NOW), [])

    def test_netvm_address_containment_fires(self):
        rule = Rule('action=drop dst4=10.137.0.0/24')
        warnings = s.detect_netvm_denied([rule], netvm_addrs=[NETVM4],
                                         now=NOW)
        self.assertEqual(codes(warnings), [s.NETVM_DENIED])
        self.assertIn(NETVM4, warnings[0].message)

    def test_exact_host_match_fires(self):
        rule = Rule('action=drop dst4=10.137.0.1/32')
        warnings = s.detect_netvm_denied([rule], netvm_addrs=[NETVM4],
                                         now=NOW)
        self.assertEqual(codes(warnings), [s.NETVM_DENIED])

    def test_non_containing_network_does_not_fire(self):
        rule = Rule('action=drop dst4=192.168.0.0/24')
        self.assertEqual(
            s.detect_netvm_denied([rule], netvm_addrs=[NETVM4], now=NOW),
            [])

    def test_without_addresses_only_the_bare_form_can_fire(self):
        # Documented limit of offline analysis: no netvm knowledge, no
        # containment verdicts — but a bare drop denies the netvm whatever
        # its address is.
        containing = Rule('action=drop dst4=10.137.0.0/24')
        self.assertEqual(s.detect_netvm_denied([containing], now=NOW), [])
        self.assertEqual(
            codes(s.detect_netvm_denied([Rule('action=drop')], now=NOW)),
            [s.NETVM_DENIED])

    def test_ipv6_containment_and_version_mismatch_safety(self):
        v6 = Rule('action=drop dst6=fd09::/64')
        self.assertEqual(
            codes(s.detect_netvm_denied([v6], netvm_addrs=[NETVM6],
                                        now=NOW)),
            [s.NETVM_DENIED])
        # An IPv4 address against an IPv6 network must neither crash
        # (TypeError from ipaddress) nor fire.
        self.assertEqual(
            s.detect_netvm_denied([v6], netvm_addrs=[NETVM4], now=NOW), [])
        v4 = Rule('action=drop dst4=10.137.0.0/24')
        self.assertEqual(
            s.detect_netvm_denied([v4], netvm_addrs=[NETVM6], now=NOW), [])

    def test_hostname_dsthost_is_undecidable_offline_and_does_not_fire(self):
        # The core resolves no names (no qubesadmin, no DNS, by design).
        # Documented gap: pass resolved addresses via netvm_addrs instead.
        rule = Rule('action=drop dsthost=gateway.local')
        self.assertEqual(
            s.detect_netvm_denied([rule], netvm_addrs=[NETVM4], now=NOW),
            [])

    def test_service_limited_drops_are_not_netvm_denials(self):
        # Boundary decision (module docstring): a drop WITH a service
        # selector denies that service everywhere, not the netvm itself —
        # warning on the common "block pings" rule would train users to
        # click through warnings. DNS-shaped cases belong to detector 2.
        for spec in ('action=drop proto=icmp',
                     'action=drop proto=tcp',
                     'action=drop proto=tcp dstports=22',
                     'action=drop dst4=10.137.0.1/32 proto=tcp '
                     'dstports=22'):
            warnings = s.detect_netvm_denied([Rule(spec)],
                                             netvm_addrs=[NETVM4], now=NOW)
            self.assertEqual(warnings, [], spec)

    def test_accept_rules_never_fire(self):
        rules = [Rule('action=accept'), Rule('action=accept dst4=0.0.0.0/0')]
        self.assertEqual(
            s.detect_netvm_denied(rules, netvm_addrs=[NETVM4], now=NOW), [])

    def test_bad_netvm_address_raises_loudly(self):
        # A silently skipped address would silently disable lockout
        # detection — caller bugs raise (module docstring).
        with self.assertRaises(ValueError):
            s.detect_netvm_denied([Rule('action=drop')],
                                  netvm_addrs=['not-an-ip'], now=NOW)


class TestDnsDeniedDetector(unittest.TestCase):
    '''Detector 2 — a rule that denies DNS while LATER rules depend on
    resolution (card bullet 2). The condition is the card's: a DNS denial
    alone does not fire; a hostname rule alone does not fire; the pair in
    THAT order does.'''

    def test_dns_drop_with_later_hostname_rule_fires(self):
        rules = [Rule('action=drop specialtarget=dns'),
                 Rule('action=accept dsthost=example.com')]
        warnings = s.detect_dns_denied(rules, now=NOW)
        self.assertEqual(codes(warnings), [s.DNS_DENIED])
        self.assertEqual(indexes(warnings), [(0, 1)])
        self.assertIn('denies DNS', warnings[0].message)

    def test_multiple_dependents_are_all_listed(self):
        rules = [Rule('action=drop specialtarget=dns'),
                 Rule('action=accept dst4=1.2.3.4'),
                 Rule('action=accept dsthost=a.example'),
                 Rule('action=accept dsthost=b.example')]
        warnings = s.detect_dns_denied(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(0, 2, 3)])

    def test_order_matters_hostname_before_denial_does_not_fire(self):
        rules = [Rule('action=accept dsthost=example.com'),
                 Rule('action=drop specialtarget=dns')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_denial_without_later_hostname_rules_does_not_fire(self):
        # The card's condition, deliberately: blocking DNS in a qube with
        # no hostname rules is a legitimate (if unusual) configuration.
        rules = [Rule('action=drop specialtarget=dns'),
                 Rule('action=accept dst4=1.2.3.4/32')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_port_53_drops_count_as_dns_denials(self):
        for spec in ('action=drop proto=udp dstports=53',
                     'action=drop proto=tcp dstports=53-53',
                     'action=drop proto=udp dstports=40-60'):
            rules = [Rule(spec), Rule('action=accept dsthost=example.com')]
            self.assertEqual(codes(s.detect_dns_denied(rules, now=NOW)),
                             [s.DNS_DENIED], spec)

    def test_port_443_drop_is_not_a_dns_denial(self):
        rules = [Rule('action=drop proto=tcp dstports=443'),
                 Rule('action=accept dsthost=example.com')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_bare_drop_all_is_a_dns_denial(self):
        # It denies everything, DNS included.
        rules = [Rule('action=drop'),
                 Rule('action=accept dsthost=example.com')]
        self.assertEqual(codes(s.detect_dns_denied(rules, now=NOW)),
                         [s.DNS_DENIED])

    def test_dsthost_narrowed_port_53_drop_still_fires(self):
        # Over-warning by design (module docstring): offline we cannot know
        # that 1.2.3.4 is not THE DNS server, and a lockout analyzer warns.
        rules = [Rule('action=drop proto=udp dst4=1.2.3.4/32 dstports=53'),
                 Rule('action=accept dsthost=example.com')]
        self.assertEqual(codes(s.detect_dns_denied(rules, now=NOW)),
                         [s.DNS_DENIED])

    def test_later_hostname_drop_rule_also_counts_as_dependent(self):
        # A hostname rule needs resolution to MATCH whatever its action —
        # decision documented in the module docstring.
        rules = [Rule('action=drop specialtarget=dns'),
                 Rule('action=drop dsthost=ads.example')]
        self.assertEqual(codes(s.detect_dns_denied(rules, now=NOW)),
                         [s.DNS_DENIED])

    def test_accept_dns_rule_is_not_a_denial(self):
        rules = [Rule('action=accept specialtarget=dns'),
                 Rule('action=accept dsthost=example.com')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_specialtarget_dependent_is_not_resolution_dependent(self):
        # specialtarget=dns matches configured servers without resolving
        # anything — it is not a "later rule depending on resolution".
        rules = [Rule('action=drop proto=udp dstports=53'),
                 Rule('action=accept specialtarget=dns')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_expired_denial_does_not_fire(self):
        rules = [Rule(None, action='drop', specialtarget='dns',
                      expire=str(PAST)),
                 Rule('action=accept dsthost=example.com')]
        self.assertEqual(s.detect_dns_denied(rules, now=NOW), [])

    def test_expired_dependent_does_not_fire_and_is_excluded(self):
        dead = Rule(None, action='accept', dsthost='dead.example',
                    expire=str(PAST))
        alive = Rule('action=accept dsthost=alive.example')
        rules = [Rule('action=drop specialtarget=dns'), dead, alive]
        warnings = s.detect_dns_denied(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(0, 2)])
        # only the dead dependent -> no warning at all
        self.assertEqual(
            s.detect_dns_denied([rules[0], dead], now=NOW), [])


class TestAcceptAllShadowDetector(unittest.TestCase):
    '''Detector 3 — an accept-all early in the list, shadowing every rule
    after it (card bullet 3). First-match evaluation order: nothing after
    a match-all accept can ever match.'''

    def test_accept_all_with_later_rules_fires(self):
        rules = [Rule('action=accept'), Rule('action=drop proto=tcp')]
        warnings = s.detect_accept_all_shadow(rules, now=NOW)
        self.assertEqual(codes(warnings), [s.ACCEPT_ALL_SHADOW])
        self.assertEqual(indexes(warnings), [(0, 1)])
        self.assertIn('accepts everything', warnings[0].message)
        self.assertIn('never match', warnings[0].message)

    def test_lone_accept_all_is_clean_the_reset_state(self):
        # `qvm-firewall reset` writes exactly this (CLI :207-210). The
        # default state of a qube must produce ZERO warnings — a detector
        # that cries wolf here would be confirmed away daily.
        self.assertEqual(
            s.detect_accept_all_shadow([Rule('action=accept')], now=NOW), [])
        self.assertEqual(s.analyze([Rule('action=accept')], now=NOW), [])

    def test_accept_all_last_shadows_nothing(self):
        rules = [Rule('action=drop proto=tcp'), Rule('action=accept')]
        self.assertEqual(s.detect_accept_all_shadow(rules, now=NOW), [])

    def test_mid_list_accept_all_lists_every_shadowed_index(self):
        rules = [Rule('action=accept proto=tcp dstports=443'),
                 Rule('action=accept'),
                 Rule('action=drop specialtarget=dns'),
                 Rule('action=drop')]
        warnings = s.detect_accept_all_shadow(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(1, 2, 3)])

    def test_two_shadowing_accept_alls_each_fire_for_their_own_tail(self):
        rules = [Rule('action=accept'), Rule('action=accept'),
                 Rule('action=drop')]
        warnings = s.detect_accept_all_shadow(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(0, 1, 2), (1, 2)])

    def test_expired_accept_all_does_not_fire(self):
        # The engine skips expired rules — an expired match-all shadows
        # nothing; detector 4 reports the expiry instead.
        rules = [Rule(None, action='accept', expire=str(PAST)),
                 Rule('action=drop')]
        self.assertEqual(s.detect_accept_all_shadow(rules, now=NOW), [])

    def test_future_expire_accept_all_fires(self):
        rules = [Rule(None, action='accept', expire=str(FUTURE)),
                 Rule('action=drop')]
        self.assertEqual(codes(s.detect_accept_all_shadow(rules, now=NOW)),
                         [s.ACCEPT_ALL_SHADOW])

    def test_selector_carrying_accepts_do_not_fire(self):
        for spec in ('action=accept proto=tcp',
                     'action=accept dst4=1.2.3.4/32',
                     'action=accept specialtarget=dns',
                     'action=accept proto=icmp icmptype=8'):
            rules = [Rule(spec), Rule('action=drop')]
            self.assertEqual(
                s.detect_accept_all_shadow(rules, now=NOW), [], spec)

    def test_zero_prefix_accept_normalizes_to_match_all(self):
        # Same serialization-view rule as detector 1: accept dst4=0.0.0.0/0
        # reaches the engine as a bare accept-all and shadows everything.
        rules = [Rule(None, action='accept', dsthost='0.0.0.0/0'),
                 Rule('action=drop proto=tcp')]
        warnings = s.detect_accept_all_shadow(rules, now=NOW)
        self.assertEqual(codes(warnings), [s.ACCEPT_ALL_SHADOW])

    def test_comment_only_accept_is_still_match_all(self):
        rules = [Rule('action=accept comment=wide open'),
                 Rule('action=drop')]
        self.assertEqual(
            codes(s.detect_accept_all_shadow(rules, now=NOW)),
            [s.ACCEPT_ALL_SHADOW])

    def test_shadowed_expired_rules_still_count_as_shadowed(self):
        # Documented decision: later rules are reported regardless of their
        # own expiry — they are still in the grid and still dead (for two
        # reasons; detector 4 adds its own warning for the expiry).
        rules = [Rule('action=accept'),
                 Rule(None, action='drop', expire=str(PAST))]
        warnings = s.detect_accept_all_shadow(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(0, 1)])


class TestExpiredDetector(unittest.TestCase):
    '''Detector 4 — rules whose expire time has already passed (card
    bullet 4). The engine already skips them (qubes/firewall.py
    qdb_entries/save) and DROPS them at the next save; the warning exists
    because the grid still shows them as if they applied.'''

    def test_past_expire_fires_with_utc_timestamp_in_message(self):
        rule = Rule(None, action='accept', proto='tcp', dstports='443',
                    expire=str(PAST))
        warnings = s.detect_expired([rule], now=NOW)
        self.assertEqual(codes(warnings), [s.RULE_EXPIRED])
        self.assertEqual(indexes(warnings), [(0,)])
        expected_iso = datetime.datetime.fromtimestamp(
            PAST, datetime.timezone.utc).isoformat()
        self.assertIn(expected_iso, warnings[0].message)

    def test_exactly_now_is_not_yet_expired(self):
        # Strictly-earlier comparison, mirroring rules.Expire.expired.
        rule = Rule(None, action='accept', expire=str(NOW))
        self.assertEqual(s.detect_expired([rule], now=NOW), [])

    def test_future_expire_does_not_fire(self):
        rule = Rule(None, action='accept', expire=str(FUTURE))
        self.assertEqual(s.detect_expired([rule], now=NOW), [])

    def test_rule_without_expire_does_not_fire(self):
        self.assertEqual(
            s.detect_expired([Rule('action=accept')], now=NOW), [])

    def test_multiple_expired_rules_report_their_own_indexes(self):
        rules = [Rule(None, action='accept', expire=str(PAST)),
                 Rule('action=accept'),
                 Rule(None, action='drop', expire=str(PAST - 1))]
        warnings = s.detect_expired(rules, now=NOW)
        self.assertEqual(indexes(warnings), [(0,), (2,)])

    def test_now_accepts_datetime_naive_utc_and_aware(self):
        rule = Rule(None, action='accept', expire='100')
        naive = datetime.datetime(2027, 1, 15, 8, 0, 0)
        aware = naive.replace(tzinfo=datetime.timezone.utc)
        self.assertEqual(
            codes(s.detect_expired([rule], now=naive)), [s.RULE_EXPIRED])
        self.assertEqual(
            codes(s.detect_expired([rule], now=aware)), [s.RULE_EXPIRED])

    def test_now_none_uses_the_wall_clock(self):
        # The ONE wall-time test: epoch 100 (1970) is expired for any
        # plausible "now", and far-future is not, on any machine.
        self.assertEqual(
            codes(s.detect_expired([Rule(None, action='accept',
                                         expire='100')])),
            [s.RULE_EXPIRED])
        self.assertEqual(
            s.detect_expired([Rule(None, action='accept',
                                   expire=str(2 ** 34))]),
            [])

    def test_bool_now_is_rejected(self):
        with self.assertRaises(TypeError):
            s.detect_expired([Rule('action=accept')], now=True)


class TestDuplicatesDetector(unittest.TestCase):
    '''Detector 5 — exact duplicates (card bullet 5). "Exact" is the
    serialized API form: the equality delete-by-match and the engine use,
    so input spelling differences that normalize away still count.'''

    def test_identical_rules_fire_with_both_indexes(self):
        rules = [Rule('action=accept proto=tcp dstports=443'),
                 Rule('action=drop'),
                 Rule('action=accept proto=tcp dstports=443')]
        warnings = s.detect_duplicates(rules)
        self.assertEqual(codes(warnings), [s.DUPLICATE_RULE])
        self.assertEqual(indexes(warnings), [(0, 2)])
        self.assertIn('action=accept proto=tcp dstports=443-443',
                      warnings[0].message)

    def test_spelling_variants_that_normalize_equal_are_duplicates(self):
        # '443' serializes as '443-443' (rules.py quirk, pinned there);
        # element order is canonicalized by the serializer; dsthost= and
        # dst4= both detect as dst4 with /32.
        pairs = [
            ('action=accept proto=tcp dstports=443',
             'action=accept proto=tcp dstports=443-443'),
            ('proto=tcp action=accept',
             'action=accept proto=tcp'),
            ('action=accept dsthost=1.2.3.4',
             'action=accept dst4=1.2.3.4'),
        ]
        for first, second in pairs:
            warnings = s.detect_duplicates([Rule(first), Rule(second)])
            self.assertEqual(codes(warnings), [s.DUPLICATE_RULE],
                             (first, second))

    def test_triple_duplicate_is_one_warning_with_all_indexes(self):
        warnings = s.detect_duplicates(
            ['action=accept', 'action=accept', 'action=accept'])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(indexes(warnings), [(0, 1, 2)])

    def test_differing_comment_is_not_a_duplicate(self):
        # Comments are part of the serialized form — two rules differing
        # only in comment are different rules to the engine and to
        # delete-by-match.
        self.assertEqual(
            s.detect_duplicates([Rule('action=accept comment=a'),
                                 Rule('action=accept comment=b')]),
            [])

    def test_differing_action_or_selector_is_not_a_duplicate(self):
        self.assertEqual(
            s.detect_duplicates([Rule('action=accept'),
                                 Rule('action=drop')]),
            [])
        self.assertEqual(
            s.detect_duplicates([Rule('action=accept proto=tcp'),
                                 Rule('action=accept proto=udp')]),
            [])

    def test_expired_duplicates_still_fire_structural_check(self):
        # Documented decision: duplication is structural; detector 4 adds
        # the expiry warnings alongside.
        rules = [Rule(None, action='accept', expire=str(PAST)),
                 Rule(None, action='accept', expire=str(PAST))]
        self.assertEqual(codes(s.detect_duplicates(rules)),
                         [s.DUPLICATE_RULE])


class TestAnalyzeComposition(unittest.TestCase):
    '''analyze() — all five detectors over the whole ordered list, before
    any write (DESIGN.md §5, card t_419e57b1: every warning must be shown
    and confirmed).'''

    def test_empty_list_analyzes_to_nothing(self):
        self.assertEqual(s.analyze([], now=NOW), [])
        self.assertEqual(s.analyze([], netvm_addrs=[NETVM4], now=NOW), [])

    def test_healthy_list_analyzes_to_nothing(self):
        rules = [Rule('action=accept specialtarget=dns'),
                 Rule('action=accept proto=tcp dst4=1.2.3.4/32 '
                      'dstports=443'),
                 Rule('action=accept proto=icmp icmptype=8')]
        self.assertEqual(s.analyze(rules, netvm_addrs=[NETVM4], now=NOW),
                         [])

    def test_detector_order_is_the_cards_order(self):
        self.assertEqual(
            s.DETECTOR_ORDER,
            (s.NETVM_DENIED, s.DNS_DENIED, s.ACCEPT_ALL_SHADOW,
             s.RULE_EXPIRED, s.DUPLICATE_RULE))

    def test_warnings_are_grouped_in_detector_order(self):
        # One list that trips four detectors: bare drop (netvm + dns via
        # the hostname rule), expired rule, duplicate pair.
        rules = [
            Rule('action=accept dsthost=example.com'),   # 0 dependent
            Rule(None, action='drop', expire=str(PAST)),  # 1 expired
            Rule('action=accept proto=tcp dstports=443'),  # 2
            Rule('action=accept proto=tcp dstports=443'),  # 3 duplicate
            Rule('action=drop'),                          # 4 bare drop
        ]
        warnings = s.analyze(rules, now=NOW)
        seen = codes(warnings)
        # group order follows DETECTOR_ORDER (not rule order)
        positions = [seen.index(code) for code in
                     (s.NETVM_DENIED, s.RULE_EXPIRED, s.DUPLICATE_RULE)
                     if code in seen]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(s.NETVM_DENIED, seen)
        self.assertIn(s.RULE_EXPIRED, seen)
        self.assertIn(s.DUPLICATE_RULE, seen)
        # rule 4 (bare drop) is AFTER the hostname rule, so detector 2
        # must not fire for it — order is the card's condition
        self.assertNotIn(s.DNS_DENIED, seen)

    def test_dns_detector_fires_within_analyze_for_earlier_denial(self):
        rules = [Rule('action=drop specialtarget=dns'),
                 Rule('action=accept dsthost=example.com')]
        self.assertIn(s.DNS_DENIED, codes(s.analyze(rules, now=NOW)))

    def test_analyze_is_the_concatenation_of_the_five_detectors(self):
        rules = RuleList([Rule('action=accept'), Rule('action=drop')])
        expected = (s.detect_netvm_denied(rules, [NETVM4], NOW)
                    + s.detect_dns_denied(rules, NOW)
                    + s.detect_accept_all_shadow(rules, NOW)
                    + s.detect_expired(rules, NOW)
                    + s.detect_duplicates(rules))
        self.assertEqual(s.analyze(rules, netvm_addrs=[NETVM4], now=NOW),
                         expected)

    def test_single_shared_clock_at_the_expiry_boundary(self):
        # analyze resolves `now` ONCE: a rule expiring exactly at now is
        # active for every detector (not expired, and its bare-drop form
        # still fires detector 1) — no detector may see a later instant.
        rule = Rule(None, action='drop', expire=str(NOW))
        warnings = s.analyze([rule], now=NOW)
        self.assertEqual(codes(warnings), [s.NETVM_DENIED])

    def test_accepts_rulelist_rules_and_api_strings(self):
        for form in (RuleList(['action=drop']),
                     [Rule('action=drop')],
                     ['action=drop']):
            self.assertEqual(codes(s.analyze(form, now=NOW)),
                             [s.NETVM_DENIED], form)

    def test_unparseable_input_raises_loudly(self):
        # analyze expects validated rules; garbage is a programming error
        # and must not be silently skipped (module docstring).
        with self.assertRaises(ValueError):
            s.analyze(['definitely not a rule'])

    def test_format_warnings_is_one_bracketed_line_each(self):
        warnings = s.analyze([Rule('action=drop'),
                              Rule(None, action='accept',
                                   expire=str(PAST))], now=NOW)
        text = s.format_warnings(warnings)
        lines = text.splitlines()
        self.assertEqual(len(lines), len(warnings))
        for line, warning in zip(lines, warnings):
            self.assertTrue(line.startswith('[{}] '.format(warning.code)))
            self.assertIn(warning.message, line)

    def test_format_warnings_of_nothing_is_the_empty_string(self):
        self.assertEqual(s.format_warnings([]), '')

    def test_safety_warning_coerces_indexes_to_tuple(self):
        warning = s.SafetyWarning(s.RULE_EXPIRED, [3, 4], 'msg')
        self.assertEqual(warning.indexes, (3, 4))
        code, idx, message = warning
        self.assertEqual((code, idx, message), (s.RULE_EXPIRED, (3, 4),
                                                'msg'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
