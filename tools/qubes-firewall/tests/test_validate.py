# Copied from qvm-firewall-ui v1.0.0 (tests/test_validate.py), with the package renamed.
# -*- coding: utf-8 -*-
'''Tier-1 tests for qubes_firewall.validate — the engine acceptance mirror.

Acceptance contract (card t_4e486699): EVERY engine acceptance rule has a
NAMED test. "Engine" spans the three layers that decide whether a rule
typed into qvm-firewall survives to disk (validate.py's module docstring
records each sha256):

- CLIENT  qubesadmin/firewall.py    — mirrored 1:1 by rules.py
- CLI     qubesadmin/tools/qvm_firewall.py — RuleAction input rules
- SERVER  qubes/firewall.py (qubes-core-admin) — qubesd re-parses every
          line of admin.vm.firewall.Set; the layer that makes
          save_rules() fail

validate.py's verdict is the INTERSECTION: valid only if all three layers
accept. Line cites below (SERVER :NNN unless noted) refer to the fetched
qubes/firewall.py; CLI :NNN to qubesadmin/tools/qvm_firewall.py.

The three documented tightenings beyond the intersection also get named
tests, each stating exactly what the layers do:

- T1 ports 1..65535 (CLIENT 0..65536 :176, SERVER 0..65535 :185)
- T2 empty dsthost rejected (SERVER passes '' vacuously :137, CLI rejects
  the trailing-'=' token :50-52)
- T3 dstname= rejected (SERVER accepts it :469, CLIENT cannot re-parse it)

Message policy is tested too: where the engine raises a clean sentence, the
checker must return it VERBATIM (TestMessageFidelity pins the constants, so
a drift away from upstream fails here).
'''

import os
import sys
import time
import unittest

# Layout-independent import shim (same contract as tests/test_rules.py):
# running this file directly from any cwd must work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qubes_firewall import validate as v
from qubes_firewall.rules import Rule, RuleList


class TestActionAcceptance(unittest.TestCase):
    '''action ∈ {accept, drop}, case-sensitive; a rule without action is
    invalid. SERVER :79-85 (Action), :286 (property_require('action')).'''

    def test_action_accepts_exactly_accept_and_drop(self):
        self.assertIsNone(v.check_action('accept'))
        self.assertIsNone(v.check_action('drop'))

    def test_action_rejects_everything_else(self):
        for bad in ('ACCEPT', 'Deny', 'reject', 'allow', 'accept drop'):
            self.assertIsNotNone(v.check_action(bad), bad)

    def test_action_charset_runs_before_membership(self):
        # SERVER RuleChoice calls the base RuleOption charset check first
        # (:66-67), so a value with unsafe characters fails as 'strange
        # characters in rule' (:44), not as an unknown action.
        self.assertEqual(v.check_action('acc ept'), v.M_STRANGE_CHARS)
        self.assertEqual(v.check_action('accept!'), v.M_STRANGE_CHARS)

    def test_missing_action_is_reported_at_every_level(self):
        # SERVER :286 property_require('action'); CLIENT 'missing action='.
        self.assertIn(v.M_MISSING_ACTION,
                      [p.message for p in v.validate_fields(proto='tcp')])
        self.assertIn(v.M_MISSING_ACTION,
                      [p.message for p in v.validate_api_line('proto=tcp')])
        self.assertIn(v.M_MISSING_ACTION,
                      [p.message for p in v.validate_api_line('')])

    def test_missing_action_problem_names_the_field(self):
        problems = v.validate_api_line('proto=tcp dstports=80')
        action_problems = [p for p in problems if p.field == 'action']
        self.assertEqual(len(action_problems), 1)
        self.assertEqual(action_problems[0].message, v.M_MISSING_ACTION)


class TestProtoAcceptance(unittest.TestCase):
    '''proto ∈ {tcp, udp, icmp}. SERVER :88-95.'''

    def test_proto_accepts_exactly_tcp_udp_icmp(self):
        for name in ('tcp', 'udp', 'icmp'):
            self.assertIsNone(v.check_proto(name), name)

    def test_proto_rejects_everything_else(self):
        for bad in ('TCP', 'sctp', 'igmp', 'all', ''):
            self.assertIsNotNone(v.check_proto(bad), bad)

    def test_proto_charset_before_membership(self):
        self.assertEqual(v.check_proto('tc!p'), v.M_STRANGE_CHARS)


class TestHostnameCharset(unittest.TestCase):
    '''Hostname charset [a-z0-9-._], lowercase only. SERVER :136-138.
    Card bullet: "hostname charset and dot count".'''

    def test_hostname_accepts_lowercase_digits_dash_dot_underscore(self):
        for good in ('example.com', 'a-b_c.d', 'x', '9.9', '-lead.ing_',
                     'under_score'):
            self.assertIsNone(v.check_dsthost(good), good)

    def test_hostname_rejects_uppercase(self):
        # SERVER hostname branch is lowercase-only (:136): 'EXAMPLE.com'
        # passes the BASE charset (ascii_letters) but fails the hostname set.
        self.assertEqual(v.check_dsthost('EXAMPLE.com'), v.M_INVALID_HOSTNAME)
        self.assertEqual(v.check_dsthost('Example.Com'), v.M_INVALID_HOSTNAME)

    def test_hostname_rejects_punctuation_and_spaces(self):
        for bad in ('example.com!', 'exa mple.com', 'ex@mple.com',
                    'example.com?', 'ex*ample'):
            self.assertEqual(v.check_dsthost(bad), v.M_INVALID_HOSTNAME, bad)

    def test_empty_dsthost_rejected_T2(self):
        # TIGHTENING T2: SERVER's charset passes '' vacuously (all() on the
        # empty sequence, :137) — an upstream quirk rules.py mirrors — but
        # the CLI rejects the 'dsthost=' token outright (trailing '=',
        # CLI :50-52) and an empty host rule is meaningless. Rejected with
        # SERVER's own message for None (:103).
        self.assertEqual(v.check_dsthost(''), v.M_EMPTY_DSTHOST)

    def test_malformed_ip_falls_back_to_hostname_quirk(self):
        # Engine quirk, MIRRORED not fixed: inet_pton fails on '1.2.3' and
        # '01.2.3.4' (leading zero), the hostname branch takes them, and
        # digits+dots are hostname-legal — so the engine ACCEPTS both as
        # hostnames. Both layers, same code. A test pins it so nobody
        # "fixes" the mirror.
        self.assertIsNone(v.check_dsthost('1.2.3'))
        self.assertIsNone(v.check_dsthost('01.2.3.4'))
        self.assertIsNone(v.check_dsthost('1.2.3.4.5'))

    def test_prefixed_malformed_ip_is_rejected_not_a_hostname(self):
        # Asymmetry of the same quirk: WITH a prefix there is no hostname
        # fallback — SERVER :166 'Invalid IP address: <host>'.
        self.assertEqual(v.check_dsthost('1.2.3/24'),
                         v.M_INVALID_IP + '1.2.3')
        self.assertEqual(v.check_dsthost('example.com/24'),
                         v.M_INVALID_IP + 'example.com')


class TestDotCountAndPrefixBounds(unittest.TestCase):
    '''Card bullets: "hostname charset and dot count" and "IPv4/IPv6 prefix
    bounds (≤32, ≤128)". SERVER DstHost :98-172.'''

    def test_ipv4_dot_count_check_mirrors_engine_position(self):
        # SERVER :121-124 checks count('.') != 3 AFTER a successful
        # inet_pton(AF_INET) — which only ever succeeds on exactly four
        # octets, so the check is defensive/unreachable by construction in
        # both upstream and this mirror. The message constant is exported
        # verbatim; the reachable sibling behaviors have their own tests
        # (hostname fallback above, Invalid IP address below).
        self.assertEqual(v.M_DOTS, 'Invalid number of dots in IPv4 address')
        self.assertIsNone(v.check_dsthost('1.2.3.4'))

    def test_bare_addresses_are_accepted(self):
        # Bare IPv4 gains /32, bare IPv6 gains /128 (SERVER :109-132);
        # normalization itself is rules.py's job and tested there.
        self.assertIsNone(v.check_dsthost('192.168.1.5'))
        self.assertIsNone(v.check_dsthost('fd09::1'))
        self.assertIsNone(v.check_dsthost('::1'))

    def test_ipv4_prefix_bounds(self):
        # Card: ≤32. SERVER :157-158 'netmask for IPv4 must be <= 32'.
        self.assertIsNone(v.check_dsthost('10.0.0.0/32'))
        self.assertIsNone(v.check_dsthost('10.0.0.0/0'))
        self.assertEqual(v.check_dsthost('10.0.0.0/33'), v.M_V4_MAX)
        self.assertEqual(v.check_dsthost('10.0.0.0/64'), v.M_V4_MAX)

    def test_ipv6_prefix_bounds(self):
        # Card: ≤128. SERVER :151-152 'netmask for IPv6 must be <= 128'.
        self.assertIsNone(v.check_dsthost('fd09::/128'))
        self.assertIsNone(v.check_dsthost('fd09::/0'))
        self.assertEqual(v.check_dsthost('fd09::/129'), v.M_V6_MAX)

    def test_prefix_must_be_digits_bad_prefix_length(self):
        # SERVER :142-143 requires str.isdigit() — stricter than CLIENT's
        # int(), which would take '+8' and ' 8'. Intersection verdict:
        # reject, with SERVER's message.
        for bad in ('10.0.0.0/+8', '10.0.0.0/-1', '10.0.0.0/abc',
                    '10.0.0.0/', '10.0.0.0/ 8', 'fd09::/x'):
            self.assertEqual(v.check_dsthost(bad), v.M_BAD_PREFIXLEN, bad)

    def test_leading_zero_prefix_is_accepted_as_digits(self):
        # '033'.isdigit() and int('033')=33 > 32 -> the bounds message, not
        # the format message; '08' is a plain 8. Mirrors SERVER's int().
        self.assertEqual(v.check_dsthost('10.0.0.0/033'), v.M_V4_MAX)
        self.assertIsNone(v.check_dsthost('10.0.0.0/08'))

    def test_too_many_slashes(self):
        # SERVER :104-105, message includes the value.
        self.assertEqual(v.check_dsthost('10.0.0.0/8/8'),
                         v.M_TOO_MANY_SLASH + '10.0.0.0/8/8')

    def test_zero_prefix_is_valid_input_normalization_is_models_job(self):
        # 0.0.0.0/0 and ::/0 are ACCEPTED values (prefix bounds are 0..32 /
        # 0..128); rules.py's serializer omits them ("any host is expressed
        # by absence", CLIENT :159-162). validate must not reject what the
        # engine accepts — safety.py then sees the normalized match-all.
        self.assertIsNone(v.check_dsthost('0.0.0.0/0'))
        self.assertIsNone(v.check_dsthost('::/0'))


class TestPortAcceptance(unittest.TestCase):
    '''Card bullet: "ports within 1-65535 with lo ≤ hi". SERVER DstPorts
    :175-197. TIGHTENING T1: CLIENT accepts 0..65536 ('port > 65536',
    CLIENT :176), SERVER accepts 0..65535 ('port >= 65536', :185), this
    module enforces 1..65535 per the card — port 0 is reserved and cannot
    be connected to.'''

    def test_single_port_and_range_accepted(self):
        for good in ('443', '1', '65535', '1200-1400', '1-65535', '53-53'):
            self.assertIsNone(v.check_dstports(good), good)

    def test_port_zero_rejected_T1(self):
        # T1, named: rules.py (CLIENT mirror) ACCEPTS '0' — see its
        # test_upstream_bound_quirk_0_and_65536_pass_the_client_model — and
        # so would qubesd; the card demands 1-65535, with SERVER's message.
        self.assertEqual(v.check_dstports('0'), v.M_PORTS_RANGE)
        self.assertEqual(v.check_dstports('0-80'), v.M_PORTS_RANGE)
        rule = Rule(None, action='accept', proto='tcp', dstports='0')
        self.assertEqual(
            [p.message for p in v.validate_rule(rule)], [v.M_PORTS_RANGE])

    def test_port_65536_rejected_both_layers_agree_verdict(self):
        # CLIENT quirk: 65536 passes the client model ('port > 65536');
        # SERVER rejects it ('port >= 65536', :185-186). Intersection:
        # reject — a rule with dstports=65536 dies at save_rules().
        self.assertEqual(v.check_dstports('65536'), v.M_PORTS_RANGE)
        rule = Rule(None, action='accept', proto='tcp', dstports='65536')
        self.assertEqual(
            [p.message for p in v.validate_rule(rule)], [v.M_PORTS_RANGE])

    def test_negative_ports_rejected(self):
        for bad in ('-1', '-5--1', '443--1'):
            self.assertIsNotNone(v.check_dstports(bad), bad)

    def test_lo_must_not_exceed_hi(self):
        # SERVER :187-188 'Invalid port range'.
        self.assertEqual(v.check_dstports('80-70'), v.M_PORT_ORDER)
        self.assertEqual(v.check_dstports('65535-1'), v.M_PORT_ORDER)

    def test_equal_bounds_are_fine(self):
        self.assertIsNone(v.check_dstports('443-443'))

    def test_format_rejections(self):
        # More than one dash: SERVER raises ValueError(value) (a bare
        # value, :183-184); non-numeric: int() raises. Message policy: our
        # sentence, engine's verdict.
        for bad in ('1-2-3', 'abc', '44a', '', '443-', '-443', '4.43',
                    '44 3'):
            self.assertEqual(v.check_dstports(bad),
                             v.M_BAD_PORT_SPEC.format(bad), bad)

    def test_engine_int_quirks_are_mirrored(self):
        # The engine parses ports through int() (SERVER :180-182, CLIENT
        # identical), so '+80', ' 80' and even '8_0' are ACCEPTED by both
        # layers — quirks mirrored, not fixed (the decision must match what
        # save_rules() survives).
        self.assertIsNone(v.check_dstports('+80'))
        self.assertIsNone(v.check_dstports(' 80'))
        self.assertIsNone(v.check_dstports('8_0'))

    def test_dstports_requires_proto_tcp_or_udp(self):
        # SERVER :338-344 (and :277-281 at construction); CLIENT setter
        # identical. Verdict AND message agree across layers.
        for fields in ({'action': 'accept', 'dstports': '443'},
                       {'action': 'accept', 'proto': 'icmp',
                        'dstports': '443'}):
            problems = v.validate_fields(**fields)
            self.assertIn(v.M_DSTPORTS_PROTO,
                          [p.message for p in problems], fields)
        # tcp and udp are fine
        for proto in ('tcp', 'udp'):
            problems = v.validate_fields(
                action='accept', proto=proto, dstports='443')
            self.assertEqual(problems, [], proto)

    def test_line_level_dstports_dependency_regardless_of_token_order(self):
        # SERVER collects kwargs first and validates dependencies at Rule
        # construction, so 'dstports=80 proto=tcp' (ports before proto) is
        # accepted, and dstports without proto at all is not.
        self.assertEqual(
            v.validate_api_line('action=accept dstports=80 proto=tcp'), [])
        problems = v.validate_api_line('action=accept dstports=80')
        self.assertIn(v.M_DSTPORTS_PROTO, [p.message for p in problems])


class TestIcmpTypeAcceptance(unittest.TestCase):
    '''Card bullet: "icmp type range". SERVER IcmpType :200-213: format
    (str.isdigit(), at most 3 characters — 'bad ICMP type') then range
    0..255 ('ICMP type out of range'), then the base charset on the RAW
    value.'''

    def test_icmp_type_range_0_to_255(self):
        self.assertIsNone(v.check_icmptype('0'))
        self.assertIsNone(v.check_icmptype('8'))
        self.assertIsNone(v.check_icmptype('255'))
        self.assertEqual(v.check_icmptype('256'), v.M_ICMP_RANGE)
        self.assertEqual(v.check_icmptype('999'), v.M_ICMP_RANGE)

    def test_icmp_type_format_is_servers_isdigit_rule(self):
        # SERVER :202-205. CLIENT is laxer (plain int()), so ' 8', '+8',
        # '-1' construct fine client-side but DIE in qubesd — intersection
        # rejects them here with SERVER's message.
        for bad in (' 8', '+8', '-1', '8.0', '', 'abc'):
            self.assertEqual(v.check_icmptype(bad), v.M_BAD_ICMP, bad)

    def test_icmp_type_over_three_digits_rejected_even_when_in_range(self):
        # '0008': CLIENT accepts (int('0008') == 8, and rules.py stores the
        # string verbatim — its quirk test pins that); SERVER rejects
        # (len > 3, :203). Named divergence: the UI must not offer a rule
        # that dies at save time.
        self.assertEqual(v.check_icmptype('0008'), v.M_BAD_ICMP)
        rule = Rule(None, action='accept', proto='icmp', icmptype='0008')
        self.assertEqual(
            [p.message for p in v.validate_rule(rule)], [v.M_BAD_ICMP])

    def test_three_digit_form_is_accepted_by_engine(self):
        # '008' passes SERVER (len 3, isdigit, int 8 in range) — mirrored.
        self.assertIsNone(v.check_icmptype('008'))

    def test_int_instances_bypass_the_format_check(self):
        # SERVER :202 isinstance(int) short-circuit; range still applies.
        self.assertIsNone(v.check_icmptype(8))
        self.assertIsNone(v.check_icmptype(0))
        self.assertEqual(v.check_icmptype(256), v.M_ICMP_RANGE)

    def test_bool_is_not_an_icmp_type(self):
        # True is an int instance, and SERVER would take it as 1 — but a
        # bool reaching a validator is a programming error, and str(True)
        # fails the format rule anyway. Rejected.
        self.assertEqual(v.check_icmptype(True), v.M_BAD_ICMP)

    def test_icmptype_requires_proto_icmp(self):
        # SERVER :347-351; CLIENT setter identical.
        for fields in ({'action': 'accept', 'icmptype': '8'},
                       {'action': 'accept', 'proto': 'tcp',
                        'icmptype': '8'}):
            problems = v.validate_fields(**fields)
            self.assertIn(v.M_ICMPTYPE_PROTO,
                          [p.message for p in problems], fields)
        problems = v.validate_fields(action='accept', proto='icmp',
                                     icmptype='8')
        self.assertEqual(problems, [])


class TestSpecialTargetAcceptance(unittest.TestCase):
    '''Card bullet: "specialtarget restricted to dns". SERVER :216-221 —
    the allowed-value set has exactly one member.'''

    def test_specialtarget_accepts_only_dns(self):
        self.assertIsNone(v.check_specialtarget('dns'))

    def test_specialtarget_rejects_everything_else(self):
        for bad in ('DNS', 'Dns', 'ntp', 'gateway', 'dns dns', ''):
            self.assertIsNotNone(v.check_specialtarget(bad), bad)

    def test_specialtarget_charset_before_membership(self):
        self.assertEqual(v.check_specialtarget('dn!'), v.M_STRANGE_CHARS)


class TestExpireAcceptance(unittest.TestCase):
    '''Card bullet: "expire parsing including +seconds". SERVER Expire
    :224-232 (digits only, 'bad expiration time'); CLI :66-69 resolves
    '+seconds' BEFORE the model; CLIENT int() is laxer (accepts '-5').'''

    def test_absolute_unix_seconds_accepted(self):
        self.assertIsNone(v.check_expire('1760000000'))
        self.assertIsNone(v.check_expire('0'))
        self.assertIsNone(v.check_expire(1760000000))

    def test_relative_plus_seconds_accepted_at_field_level(self):
        # CLI :66-69: '+300' means now+300. Field-level only — see the
        # wire-level test below.
        self.assertIsNone(v.check_expire('+300'))
        self.assertIsNone(v.check_expire('+0'))
        resolved = v.resolve_expire('+300')
        self.assertAlmostEqual(resolved, int(time.time()) + 300, delta=5)

    def test_relative_garbage_rejected(self):
        # CLI: int(value[1:]) raises on garbage — same verdict here.
        for bad in ('+', '+abc', '+ 3x', '+1.5'):
            self.assertEqual(v.check_expire(bad),
                             v.M_BAD_EXPIRE_REL.format(bad), bad)

    def test_negative_relative_delta_mirrors_cli_acceptance(self):
        # CLI int('-5') parses, so '+-5' resolves to now-5 and is ACCEPTED
        # end to end (the rule is simply born expired — safety.py's
        # detect_expired is the layer that tells the user).
        self.assertIsNone(v.check_expire('+-5'))
        self.assertLess(v.resolve_expire('+-5'), int(time.time()))

    def test_negative_absolute_rejected_server_divergence(self):
        # '-5': CLIENT accepts (int('-5') works, fromtimestamp(-5) is 1969)
        # — rules.py constructs it happily; SERVER rejects ('-5'.isdigit()
        # is False, :227-231). Intersection: reject, SERVER's message.
        self.assertEqual(v.check_expire('-5'), v.M_BAD_EXPIRE)
        rule = Rule(None, action='accept', expire='-5')
        self.assertEqual(
            [p.message for p in v.validate_rule(rule)], [v.M_BAD_EXPIRE])

    def test_non_digit_absolute_rejected(self):
        for bad in ('123.5', 'abc', '', '1e9'):
            self.assertEqual(v.check_expire(bad), v.M_BAD_EXPIRE, bad)

    def test_unrepresentable_timestamp_rejected(self):
        # fromtimestamp must not raise (SERVER :232 runs it at parse time,
        # so qubesd dies on the value; we report instead).
        self.assertEqual(v.check_expire('9' * 20),
                         v.M_EXPIRE_UNREPRESENTABLE.format('9' * 20))

    def test_wire_level_rejects_plus_seconds_with_engine_charset(self):
        # '+seconds' NEVER appears in an Admin-API line: CLI and rules.Rule
        # resolve it before serialization, and SERVER's base charset (:41)
        # has no '+' — Expire runs super().__init__ on the RAW value
        # (:226), so qubesd rejects 'expire=+300' as 'strange characters
        # in rule'. check_expire_api mirrors that, check order included.
        self.assertEqual(v.check_expire_api('+300'), v.M_STRANGE_CHARS)
        problems = v.validate_api_line('action=accept expire=+300')
        self.assertIn(v.M_STRANGE_CHARS, [p.message for p in problems])
        # ... while 'abc' passes the charset (letters are safe) and fails
        # the isdigit check, exactly as SERVER orders the two (:226-231).
        self.assertEqual(v.check_expire_api('abc'), v.M_BAD_EXPIRE)

    def test_bool_is_rejected_as_programming_error(self):
        # SERVER would take True as epoch 1 (isinstance int). A bool in a
        # UI field is a bug, not input — rejected explicitly.
        self.assertEqual(v.check_expire(True), v.M_BAD_EXPIRE)


class TestCommentAcceptance(unittest.TestCase):
    '''SERVER Comment :247-256: charset [A-Za-z0-9:;,./-_[] plus space],
    'strange characters comment'. CLIENT accepts ANY text and rules.py
    serializes it happily — this checker is what keeps the UI from writing
    a comment qubesd will refuse at save_rules() time.'''

    def test_comment_accepts_the_engine_charset(self):
        for good in ('hello world', 'a:b;c,d.e/f-g_h[i]', 'x', 'UPPER case',
                     'dots...and, commas; slashes/ dashes- braces[]'):
            self.assertIsNone(v.check_comment(good), good)

    def test_comment_rejects_equals_sign_divergence(self):
        # Named divergence: CLIENT's Comment is unrestricted (rules.py has
        # test_comment_may_contain_keyword_looking_text); SERVER rejects
        # '=' — it is not in the comment safe set (:252). A comment 'a=b'
        # constructs fine and dies at save. Intersection: reject.
        self.assertEqual(v.check_comment('a=b'), v.M_STRANGE_COMMENT)
        rule = Rule(None, action='accept', comment='a=b')
        self.assertEqual(
            [p.message for p in v.validate_rule(rule)],
            [v.M_STRANGE_COMMENT])

    def test_comment_rejects_other_unsafe_characters(self):
        for bad in ('hi!', 'why?', 'a(b)', '50%', 'quote"', "apos'",
                    'unicode-ä'):
            self.assertEqual(v.check_comment(bad), v.M_STRANGE_COMMENT, bad)

    def test_empty_comment_accepted_and_dropped_by_the_model(self):
        # SERVER passes '' vacuously; CLIENT drops an empty comment at
        # serialization (quirk pinned in rules.py's
        # test_empty_comment_is_dropped_upstream_quirk). Not an error here;
        # the UI sees it vanish exactly like qvm-firewall does.
        self.assertIsNone(v.check_comment(''))


class TestGlobalOptionCharset(unittest.TestCase):
    '''SERVER RuleOption base :39-44: every option value passes
    [A-Za-z0-9:;,./-_[]] or fails as 'strange characters in rule'. The
    per-field branches are usually stricter; this pins the base itself.'''

    def test_base_charset_constant_matches_upstream(self):
        import string as _string
        expected = frozenset(_string.ascii_letters + _string.digits
                             + ':;,./-_[]')
        self.assertEqual(v._OPTION_SAFE_SET, expected)

    def test_comment_charset_is_base_plus_space(self):
        self.assertEqual(v._COMMENT_SAFE_SET, v._OPTION_SAFE_SET | {' '})

    def test_non_ascii_digits_reach_the_charset_check(self):
        # SERVER runs the base charset on the RAW value AFTER its own
        # branch checks (:168, :209, :226): an exotic isdigit() character
        # (non-ASCII digit) passes format checks and dies at the charset.
        self.assertEqual(v.check_icmptype('\u0668'), v.M_STRANGE_CHARS)  # ٨
        self.assertEqual(v.check_dsthost('10.0.0.0/\u0668'),
                         v.M_STRANGE_CHARS)


class TestValidateFields(unittest.TestCase):
    '''Dialog-level entry point: raw field values in, Problems out, no
    construction, no tracebacks (card t_419e57b1 surfaces these).'''

    def test_clean_full_field_set(self):
        problems = v.validate_fields(
            action='accept', proto='tcp', dsthost='10.0.0.0/8',
            dstports='443', specialtarget=None, icmptype=None,
            expire='+300', comment='via dialog')
        self.assertEqual(problems, [])

    def test_none_means_absent_and_is_skipped(self):
        # rules.py mirrors the upstream quirk that setting action/
        # specialtarget/expire to None RAISES; the UI passes None for
        # cleared fields and validate_fields skips them.
        problems = v.validate_fields(action='drop', proto=None,
                                     dsthost=None, comment=None)
        self.assertEqual(problems, [])

    def test_collects_every_problem_not_just_the_first(self):
        problems = v.validate_fields(action='nope', proto='nope',
                                     dsthost='BAD HOST!')
        messages = [p.message for p in problems]
        self.assertEqual(len(messages), 3)
        self.assertIn(v.M_INVALID_HOSTNAME, messages)

    def test_dependency_problems_carry_the_dependent_field(self):
        problems = v.validate_fields(action='accept', dstports='443')
        dep = [p for p in problems if p.message == v.M_DSTPORTS_PROTO]
        self.assertEqual(len(dep), 1)
        self.assertEqual(dep[0].field, 'dstports')
        self.assertEqual(dep[0].value, '443')

    def test_unknown_field_keyword_is_a_type_error(self):
        # Programmer error, not user input: loud, not a Problem.
        with self.assertRaises(TypeError):
            v.validate_fields(action='accept', dstname='x')

    def test_field_value_and_index_defaults(self):
        problems = v.validate_fields(action='nope')
        self.assertEqual(problems[0].field, 'action')
        self.assertEqual(problems[0].value, 'nope')
        self.assertIsNone(problems[0].index)


class TestValidateRule(unittest.TestCase):
    '''Pre-save gate over CONSTRUCTED rules: catches everything the CLIENT
    mirror lets through that qubesd would reject (rules.py is deliberately
    a faithful client mirror — validate.py is the intersection gate).'''

    def test_clean_rule_has_no_problems(self):
        rule = Rule('action=accept proto=tcp dst4=1.2.3.4/32 '
                    'dstports=443-443 comment=fine, yes')
        self.assertEqual(v.validate_rule(rule), [])

    def test_catches_every_client_looser_than_engine_case(self):
        # Each of these constructs fine through rules.py (CLIENT mirror)
        # and would die in qubesd at save_rules() time.
        cases = [
            (Rule(None, action='accept', proto='tcp', dstports='65536'),
             v.M_PORTS_RANGE),
            (Rule(None, action='accept', proto='icmp', icmptype='0008'),
             v.M_BAD_ICMP),
            (Rule(None, action='accept', expire='-5'), v.M_BAD_EXPIRE),
            (Rule(None, action='accept', comment='x!y'),
             v.M_STRANGE_COMMENT),
        ]
        for rule, message in cases:
            self.assertEqual([p.message for p in v.validate_rule(rule)],
                             [message], rule.rule)

    def test_zero_prefix_dsthost_rule_is_valid_input(self):
        # dst4=0.0.0.0/0 constructs, serializes as a bare rule (the
        # element's API form is None) and the engine accepts that line.
        rule = Rule(None, action='drop', dsthost='0.0.0.0/0')
        self.assertEqual(v.validate_rule(rule), [])
        self.assertEqual(v.validate_api_line(rule.rule), [])

    def test_api_form_string_delegates_to_line_validation(self):
        problems = v.validate_rule('action=accept action=drop')
        self.assertIn(v.M_OPTION_ALREADY_SET.format('action'),
                      [p.message for p in problems])

    def test_wrong_type_is_a_type_error(self):
        with self.assertRaises(TypeError):
            v.validate_rule(42)


class TestValidateApiLine(unittest.TestCase):
    '''Raw Admin-API line mirror: SERVER Rule.from_api_string :446-478 plus
    the CLI input rules, per the intersection.'''

    def test_clean_line_is_accepted(self):
        line = ('action=accept proto=tcp dst4=1.2.3.4/32 dstports=443-443 '
                'comment=ok, yes')
        self.assertEqual(v.validate_api_line(line), [])

    def test_reset_state_line_is_accepted(self):
        self.assertEqual(v.validate_api_line('action=accept'), [])

    def test_duplicate_option_rejected_server_divergence(self):
        # SERVER :461-464 rejects duplicates ("Option 'action' already
        # set"); CLIENT collapses them last-wins (rules.py pins that). A
        # duplicated line never reaches qubesd through our own writer
        # (serialization is canonical), but raw imports must be gated.
        problems = v.validate_api_line('action=accept action=drop')
        self.assertIn(v.M_OPTION_ALREADY_SET.format('action'),
                      [p.message for p in problems])

    def test_dst4_dst6_are_dsthost_synonyms_and_collide(self):
        # SERVER :469-474: dst4/dst6 map to dsthost; a second host option
        # reports the COLLISION under the canonical name (:470-473).
        self.assertEqual(
            v.validate_api_line('action=accept dst4=1.2.3.4'), [])
        problems = v.validate_api_line(
            'action=accept dst4=1.2.3.4 dst6=fd09::1')
        self.assertIn(v.M_OPTION_ALREADY_SET.format('dsthost'),
                      [p.message for p in problems])
        problems = v.validate_api_line(
            'action=accept dst4=1.2.3.4 dst4=5.6.7.8')
        self.assertIn(v.M_OPTION_ALREADY_SET.format('dsthost'),
                      [p.message for p in problems])

    def test_dstname_rejected_T3(self):
        # TIGHTENING T3, named: SERVER accepts dstname as a third synonym
        # (:469), but CLIENT cannot parse it back ('Unknown rule elements')
        # — accepting it would create rules this UI cannot round-trip, and
        # qvm-firewall itself rejects it. Intersection: reject.
        problems = v.validate_api_line('action=accept dstname=example.com')
        self.assertIn(v.M_UNKNOWN_OPTION, [p.message for p in problems])

    def test_unknown_option_rejected(self):
        # SERVER :475-476.
        for token in ('target=x', 'src=1.2.3.4', 'Action=accept'):
            problems = v.validate_api_line('action=accept ' + token)
            self.assertIn(v.M_UNKNOWN_OPTION,
                          [p.message for p in problems], token)

    def test_double_space_rejected_server_divergence(self):
        # SERVER splits options with strip().split(' ') (:459) — a double
        # space yields an empty token and dies in the unpack; CLIENT
        # collapses spaces (rules.py pins that). Intersection: reject.
        problems = v.validate_api_line('action=accept  proto=tcp')
        self.assertIn(v.M_EMPTY_ELEMENT, [p.message for p in problems])

    def test_token_without_equals_rejected(self):
        # SERVER's unpack raises on it; the CLI's positional grammar is
        # command-line sugar that never reaches an API line.
        problems = v.validate_api_line('action=accept drop')
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].message,
                         v.M_ELEMENT_WITHOUT_EQUALS.format('drop'))

    def test_empty_value_tokens_rejected(self):
        # CLI rejects any token ending in '=' (CLI :50-52); dsthost= gets
        # T2's message, the others fail their field checks.
        problems = v.validate_api_line('action=accept dsthost=')
        self.assertIn(v.M_EMPTY_DSTHOST, [p.message for p in problems])
        problems = v.validate_api_line('action= proto=tcp')
        self.assertEqual(
            [p.field for p in problems if p.message != v.M_MISSING_ACTION],
            ['action'])

    def test_comment_partition_semantics(self):
        # Everything after the FIRST 'comment=' is free text and is not
        # tokenized (SERVER :450-452, CLIENT identical) — keyword-looking
        # comment text must not create options... but the comment charset
        # still applies, so '=' inside a comment is rejected (SERVER).
        self.assertEqual(
            v.validate_api_line('action=accept comment=proto=tcp'),
            [v.Problem('comment', 'proto=tcp', v.M_STRANGE_COMMENT)])
        self.assertEqual(
            v.validate_api_line('action=accept comment=just words'), [])

    def test_empty_comment_in_line_is_dropped_not_an_error(self):
        # SERVER 'if untrusted_comment:' (:456) and CLIENT both drop it;
        # rules.py pins the quirk.
        self.assertEqual(v.validate_api_line('action=accept comment='), [])

    def test_line_dependencies_checked_after_collection(self):
        problems = v.validate_api_line(
            'action=accept proto=icmp dstports=443')
        self.assertIn(v.M_DSTPORTS_PROTO, [p.message for p in problems])
        problems = v.validate_api_line('action=accept proto=tcp icmptype=8')
        self.assertIn(v.M_ICMPTYPE_PROTO, [p.message for p in problems])

    def test_carriage_return_inside_a_line_rejected(self):
        # splitlines() consumes CRLF line endings (see the text-level
        # test), but a stray \r INSIDE a line lands in a value and fails
        # the engine charset.
        problems = v.validate_api_line('action=accept\r proto=tcp')
        self.assertIn(v.M_STRANGE_CHARS, [p.message for p in problems])

    def test_non_string_is_a_type_error(self):
        with self.assertRaises(TypeError):
            v.validate_api_line(42)


class TestValidateApiTextAndRuleList(unittest.TestCase):
    '''Whole-payload validation with Problem.index set — the shape the
    adapter's pre-save gate consumes.'''

    def test_clean_payload_from_the_model_validates_empty(self):
        rules = RuleList(['action=accept proto=tcp dstports=443',
                          'action=drop dst4=10.0.0.0/8'])
        self.assertEqual(v.validate_api_text(rules.to_api_text()), [])

    def test_trailing_newline_is_not_a_phantom_blank_line(self):
        # to_api_text ends every rule with '\n'; splitlines semantics mean
        # no extra empty last line (a blank line WOULD be an error, as
        # rules.py's test_blank_line_is_a_parse_error pins for the model).
        self.assertEqual(v.validate_api_text('action=accept\n'), [])
        problems = v.validate_api_text('action=accept\n\n')
        self.assertEqual({p.index for p in problems}, {1})

    def test_problems_carry_line_indexes(self):
        text = 'action=accept\naction=nope\naction=drop\n'
        problems = v.validate_api_text(text)
        self.assertEqual([p.index for p in problems], [1])
        self.assertEqual(problems[0].field, 'action')

    def test_crlf_payload_is_consumed_by_splitlines(self):
        # Same split as RuleList.from_api_text (str.splitlines): '\r\n' is
        # ONE boundary, so a CRLF capture validates clean — model and
        # validator can never disagree about line splitting.
        self.assertEqual(
            v.validate_api_text('action=accept\r\naction=drop\r\n'), [])

    def test_rule_list_validation_sets_indexes(self):
        rules = RuleList([
            'action=accept',
            Rule(None, action='accept', proto='tcp', dstports='0'),
        ])
        problems = v.validate_rule_list(rules)
        self.assertEqual([(p.index, p.message) for p in problems],
                         [(1, v.M_PORTS_RANGE)])

    def test_rule_list_accepts_plain_iterables(self):
        problems = v.validate_rule_list(
            ['action=accept', Rule(None, action='accept', comment='x!')])
        self.assertEqual([p.index for p in problems], [1])

    def test_unparseable_garbage_raises_not_reports(self):
        # Documented split of duties: raw text goes through
        # validate_api_text (reports); validate_rule_list coerces through
        # RuleList, so garbage raises ValueError from the model — loud,
        # because by that point the caller promised parseable rules.
        with self.assertRaises(ValueError):
            v.validate_rule_list(['not a rule'])

    def test_problem_namedtuple_shape(self):
        problem = v.Problem('action', 'nope', 'msg')
        field, value, message, index = problem
        self.assertEqual((field, value, message, index),
                         ('action', 'nope', 'msg', None))
        replaced = problem._replace(index=3)
        self.assertEqual(replaced.index, 3)
        self.assertEqual(replaced.message, 'msg')


class TestMessageFidelity(unittest.TestCase):
    '''Every engine message constant is the upstream sentence VERBATIM
    (message policy in the module docstring). Cites: qubes/firewall.py
    (fetched 2026-10-15, sha256 df4eb508d459c62adfca8a5663a702d934be46932aa0
    2723e59d16248ccc45db). A drift away from upstream fails HERE.'''

    def test_verbatim_engine_messages(self):
        expected = {
            'M_STRANGE_CHARS': ('strange characters in rule', 44),
            'M_STRANGE_COMMENT': ('strange characters comment', 255),
            'M_EMPTY_DSTHOST': ('Destination address cannot be empty', 103),
            'M_BAD_PREFIXLEN': ('bad prefix length', 143),
            'M_NETMASK_NONNEG': ('netmask must be non-negative', 146),
            'M_V6_BETWEEN': ('netmask for IPv6 must be between 0 and 128',
                             113),
            'M_V4_BETWEEN': ('netmask for IPv4 must be between 0 and 32',
                             128),
            'M_V6_MAX': ('netmask for IPv6 must be <= 128', 152),
            'M_V4_MAX': ('netmask for IPv4 must be <= 32', 158),
            'M_DOTS': ('Invalid number of dots in IPv4 address', 122),
            'M_INVALID_HOSTNAME': ('Invalid hostname', 138),
            'M_PORTS_RANGE': ('Ports out of range', 186),
            'M_PORT_ORDER': ('Invalid port range', 188),
            'M_BAD_ICMP': ('bad ICMP type', 205),
            'M_ICMP_RANGE': ('ICMP type out of range', 208),
            'M_BAD_EXPIRE': ('bad expiration time', 231),
            'M_DSTPORTS_PROTO':
                ("dstports valid only for 'tcp' and 'udp' protocols", 342),
            'M_ICMPTYPE_PROTO':
                ("icmptype valid only for 'icmp' protocol", 351),
            'M_MISSING_ACTION': ('missing action=', 286),
            'M_UNKNOWN_OPTION': ('Unknown firewall option', 476),
        }
        for name, (message, _line) in expected.items():
            self.assertEqual(getattr(v, name), message, name)

    def test_parameterized_engine_message_shapes(self):
        self.assertEqual(v.M_TOO_MANY_SLASH, 'Too many /: ')   # :105
        self.assertEqual(v.M_INVALID_IP, 'Invalid IP address: ')  # :166
        self.assertEqual(v.M_OPTION_ALREADY_SET.format('action'),
                         "Option 'action' already set")        # :462-463

    def test_checker_map_covers_every_field(self):
        self.assertEqual(set(v.CHECKERS), set(v.FIELDS))
        self.assertEqual(set(v._LINE_CHECKERS), set(v.FIELDS))
        # the only input/wire divergence is expire (+seconds, CLI :66-69)
        differing = [f for f in v.FIELDS
                     if v.CHECKERS[f] is not v._LINE_CHECKERS[f]]
        self.assertEqual(differing, ['expire'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
