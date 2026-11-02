# Copied unchanged from qvm-firewall-ui v1.0.0 (qvm_firewall_ui/rules.py, commit 9ab90bb).
# Keep it in step with that file rather than editing it here.
# -*- coding: utf-8 -*-
'''Firewall rule model: parse, serialize, order. Standard library only.

This module mirrors ``qubesadmin.firewall`` from
QubesOS/qubes-core-admin-client (``main``, fetched 2026-10-15,
firewall.py sha256 9e67c1f4cdebad79307df53724e558566927dfdda42e9eba17d0e028bfae170d,
tools/qvm_firewall.py sha256 93919833923eb32da659267da42509282b7b5df6c64cb35fa28e5f2187057467)
— the fact/source table is DESIGN.md §2, and ADR-2 forbids re-deriving what
upstream already defines. Where this file and a memory of "how firewall rules
work" disagree, this file follows upstream, quirks included; the quirks are
called out inline and have named tests so nobody "fixes" them by accident.

Serialization is the Admin API form, one rule per line, e.g.::

    action=accept proto=tcp dst4=10.0.0.0/8 dstports=443-443 comment=free text

``Rule.rule`` produces exactly the string ``qubesadmin.firewall.Rule.rule``
produces, and ``RuleList.to_api_text`` exactly the payload
``Firewall.save_rules`` sends to ``admin.vm.firewall.Set``.

Documented deviations from upstream (all recorded in the task handoff):

1. ``Rule.__eq__`` against a ``str`` compares the serialized forms. Upstream
   compares ``self.rule == str`` — the builtin type object — which is an
   obvious typo that makes string comparison always False.
2. ``+seconds`` relative expire is resolved to absolute epoch seconds in the
   ``Rule.expire`` setter. Upstream resolves it in the qvm-firewall CLI
   (``RuleAction``) and the model only ever sees absolute seconds; this model
   is the UI's single rule-construction path, so the CLI's resolution lives
   here. Serialized form is always absolute — round-trips stay byte-stable.
3. ``RuleList`` index operations are strict: zero-based (``list`` numbers
   rules from 0), non-negative, bounds-checked. The CLI feeds ``--before`` /
   ``--rule-no`` straight to ``list.insert`` / ``list.pop``, which silently
   accept negative and past-the-end indexes. Here the index is evaluation
   order and must be explicit, never implied (DESIGN.md §5).

Out of scope, deliberately: the CLI's positional grammar
``<action> [<dsthost> [<proto> [<dstports>|<icmptype>]]]`` is qvm-firewall
command-line sugar (``RuleAction``) that builds the same keyword rule; this
module models the API form. ``validate.py`` (engine acceptance rules) and
``safety.py`` (lockout analysis) are separate deliverables and import this
module.
'''

import datetime
import socket
import string
import time

__all__ = [
    'RuleOption', 'RuleChoice', 'Action', 'Proto', 'DstHost', 'DstPorts',
    'IcmpType', 'SpecialTarget', 'Expire', 'Comment', 'Rule', 'RuleList',
]


class RuleOption:
    '''Base class for a single rule element (mirrors upstream RuleOption).'''

    def __init__(self, value):
        self._value = str(value)

    @property
    def rule(self):
        '''API representation of this rule element.

        ``None`` means "omit this element from the serialized rule" (see
        DstHost for the only case where that happens).'''
        raise NotImplementedError

    @property
    def pretty_value(self):
        '''Human readable representation (the qvm-firewall table cell).'''
        return str(self)

    def __str__(self):
        return self._value

    def __eq__(self, other):
        # Mirrors upstream: element options compare equal to their string
        # form, which is what makes `proto not in ('tcp', 'udp')` work.
        return str(self) == other


class RuleChoice(RuleOption):
    '''Base class for multiple-choice rule elements.

    Upstream builds ``allowed_values`` by scanning the class dict for string
    attributes; the semantics mirrored here are the closed value sets
    themselves. An invalid value raises ``ValueError(value)`', as upstream.'''

    allowed_values = ()

    def __init__(self, value):
        super().__init__(value)
        if value not in self.allowed_values:
            raise ValueError(value)


class Action(RuleChoice):
    '''Rule action: accept or drop (case-sensitive, exactly as upstream).'''

    allowed_values = ('accept', 'drop')

    @property
    def rule(self):
        return 'action=' + str(self)


class Proto(RuleChoice):
    '''Protocol name: tcp, udp or icmp.'''

    allowed_values = ('tcp', 'udp', 'icmp')

    @property
    def rule(self):
        return 'proto=' + str(self)


class DstHost(RuleOption):
    '''Destination host/network: IPv4, IPv6, or DNS name.

    Mirrors upstream DstHost exactly, quirks included:

    - a bare IPv4 gains ``/32``, a bare IPv6 gains ``/128``;
    - with an explicit ``/prefix``, the address part must be an IP —
      hostnames cannot carry a prefix (``ValueError: Invalid IP address``);
    - hostnames are restricted to ``[a-z0-9-._]`` — lowercase only, no
      spaces, and (upstream quirk) the empty string passes ``all()``;
    - ``prefixlen == 0`` on an IP (``0.0.0.0/0``, ``::/0``) serializes to
      ``None``: "any host" is expressed by absence, not by a zero prefix;
    - the serialized keyword is the detected type: ``dst4=``, ``dst6=`` or
      ``dsthost=`` — so parsing ``dsthost=1.2.3.4`` re-serializes as
      ``dst4=1.2.3.4/32``.
    '''

    def __init__(self, value, prefixlen=None):
        # The structure below is upstream's verbatim (socket.inet_pton based,
        # not the ipaddress module), including its exact error messages and
        # their order of raising.
        if value.count('/') > 1:
            raise ValueError('Too many /: ' + value)
        if not value.count('/'):
            # add prefix length to bare IP addresses
            try:
                socket.inet_pton(socket.AF_INET6, value)
                if prefixlen is not None:
                    self.prefixlen = prefixlen
                else:
                    self.prefixlen = 128
                if self.prefixlen < 0 or self.prefixlen > 128:
                    raise ValueError(
                        'netmask for IPv6 must be between 0 and 128')
                value += '/' + str(self.prefixlen)
                self.type = 'dst6'
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET, value)
                    if value.count('.') != 3:
                        raise ValueError(
                            'Invalid number of dots in IPv4 address')
                    if prefixlen is not None:
                        self.prefixlen = prefixlen
                    else:
                        self.prefixlen = 32
                    if self.prefixlen < 0 or self.prefixlen > 32:
                        raise ValueError(
                            'netmask for IPv4 must be between 0 and 32')
                    value += '/' + str(self.prefixlen)
                    self.type = 'dst4'
                except socket.error:
                    self.type = 'dsthost'
                    self.prefixlen = 0
                    safe_set = string.ascii_lowercase + string.digits + '-._'
                    if not all(c in safe_set for c in value):
                        raise ValueError('Invalid hostname')
        else:
            host, prefixlen_str = value.split('/', 1)
            prefixlen = int(prefixlen_str)
            if prefixlen < 0:
                raise ValueError('netmask must be non-negative')
            self.prefixlen = prefixlen
            try:
                socket.inet_pton(socket.AF_INET6, host)
                if prefixlen > 128:
                    raise ValueError('netmask for IPv6 must be <= 128')
                self.type = 'dst6'
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET, host)
                    if prefixlen > 32:
                        raise ValueError('netmask for IPv4 must be <= 32')
                    self.type = 'dst4'
                    if host.count('.') != 3:
                        raise ValueError(
                            'Invalid number of dots in IPv4 address')
                except socket.error:
                    raise ValueError('Invalid IP address: ' + host)

        super().__init__(value)

    @property
    def rule(self):
        if self.prefixlen == 0 and self.type != 'dsthost':
            # 0.0.0.0/0 or ::/0, doesn't limit to any particular host,
            # so skip it (upstream comment, upstream behavior).
            return None
        return self.type + '=' + str(self)


class DstPorts(RuleOption):
    '''Destination port(s), for TCP/UDP only.

    Mirrors upstream DstPorts verbatim, including two details that matter for
    byte-stability and for validate.py:

    - the API form is ALWAYS the ``lo-hi`` range: a single port ``443``
      serializes as ``dstports=443-443``, while ``str()``/``pretty_value``
      (the CLI table cell) collapses equal bounds back to ``443``;
    - the bound check is upstream's: ``port < 0 or port > 65536`` raises.
      The client library therefore accepts 0 and 65536; the engine's
      stricter acceptance rule (1-65535) is mirrored in validate.py, not
      here — this module's contract is "behave like qubesadmin.firewall".
    '''

    def __init__(self, value):
        value = str(value) if isinstance(value, int) else value
        if value.count('-') == 1:
            self.range = [int(x) for x in value.split('-', 1)]
        elif not value.count('-'):
            self.range = [int(value), int(value)]
        else:
            raise ValueError(value)
        if any(port < 0 or port > 65536 for port in self.range):
            raise ValueError('Ports out of range')
        if self.range[0] > self.range[1]:
            raise ValueError('Invalid port range')
        super().__init__(
            str(self.range[0]) if self.range[0] == self.range[1]
            else '{!s}-{!s}'.format(*self.range))

    @property
    def rule(self):
        return 'dstports=' + '{!s}-{!s}'.format(*self.range)


class IcmpType(RuleOption):
    '''ICMP packet type: integer 0-255, anything else is a ValueError.

    Upstream quirk, mirrored: the stored value is ``str(value)``, so
    ``IcmpType('008')`` serializes as ``icmptype=008``. The value is not
    re-normalized through ``int()``.'''

    def __init__(self, value):
        super().__init__(value)
        value = int(value)
        if value < 0 or value > 255:
            raise ValueError('ICMP type out of range')

    @property
    def rule(self):
        return 'icmptype=' + str(self)


class SpecialTarget(RuleChoice):
    '''Special destination. Upstream accepts exactly one value: dns.'''

    allowed_values = ('dns',)

    @property
    def rule(self):
        return 'specialtarget=' + str(self)


class Expire(RuleOption):
    '''Rule expire time: absolute UNIX epoch seconds.

    ``+seconds`` relative form is resolved before construction — by the
    ``Rule.expire`` setter here (upstream: by the qvm-firewall CLI). This
    class mirrors upstream Expire: ``int(value)`` must succeed, and the
    serialized form is always absolute.'''

    def __init__(self, value):
        super().__init__(value)
        self.datetime = datetime.datetime.fromtimestamp(int(value),
                                                        datetime.timezone.utc)

    @property
    def rule(self):
        return 'expire=' + str(self)

    @property
    def expired(self):
        '''Has this rule expired already?'''
        return self.datetime < datetime.datetime.now(datetime.timezone.utc)

    @property
    def pretty_value(self):
        '''Human readable representation, e.g. "+297s" (CLI table cell).'''
        now = datetime.datetime.now(datetime.timezone.utc)
        duration = (self.datetime - now).total_seconds()
        return "{:+.0f}s".format(duration)


class Comment(RuleOption):
    '''User comment: free text, may contain spaces and "=" characters.'''

    @property
    def rule(self):
        return 'comment=' + str(self)


# Upstream Rule.__init__ applies elements in this fixed order, regardless of
# their order in the input string. The order is load-bearing: proto must be
# set before dstports/icmptype so their protocol checks see the final proto.
RULE_PARSE_ORDER = ('action', 'proto', 'dsthost', 'dst4', 'dst6',
                    'specialtarget', 'dstports', 'icmptype', 'expire',
                    'comment')

# Upstream Rule.rule serializes in this fixed order; comment must be last
# (it is free text and would swallow the rest of the line on re-parse).
RULE_SERIALIZE_ORDER = ('action', 'proto', 'dsthost', 'dstports', 'icmptype',
                        'specialtarget', 'expire', 'comment')


class Rule:
    '''A single firewall rule, mirroring qubesadmin.firewall.Rule.

    Construct from an API-form string (``Rule('action=accept proto=tcp '
    'dstports=443')``), from keywords (``Rule(None, action='accept')``), or
    from both — keyword arguments override parsed elements, as upstream.
    A rule with no action is invalid (``ValueError: missing action=``).'''

    def __init__(self, rule=None, **kwargs):
        '''Single firewall rule.

        :param rule: API-form rule string, or None
        :param kwargs: rule elements, overriding the parsed string
        '''
        self._action = None
        self._proto = None
        self._dsthost = None
        self._dstports = None
        self._icmptype = None
        self._specialtarget = None
        self._expire = None
        self._comment = None

        rule_dict = {}
        if rule is not None:
            # comment= is split off FIRST: everything after the first
            # 'comment=' is free text and must not be tokenized.
            rule_opts, _, comment = rule.partition('comment=')
            # Upstream: an element without '=' makes dict() raise ValueError;
            # duplicate keys collapse, last one wins.
            rule_dict = dict(rule_opt.split('=', 1) for rule_opt in
                             rule_opts.split(' ') if rule_opt)
            if comment:
                # Upstream quirk, mirrored: an empty comment ('comment=' with
                # nothing after it) is falsy and therefore dropped.
                rule_dict['comment'] = comment
        rule_dict.update(kwargs)

        for rule_opt in RULE_PARSE_ORDER:
            value = rule_dict.pop(rule_opt, None)
            if value is None:
                continue
            if rule_opt in ('dst4', 'dst6'):
                rule_opt = 'dsthost'
            setattr(self, rule_opt, value)

        if rule_dict:
            raise ValueError('Unknown rule elements: {!r}'.format(rule_dict))

        if self.action is None:
            raise ValueError('missing action=')

    @property
    def action(self):
        '''rule action'''
        return self._action

    @action.setter
    def action(self, value):
        # Upstream quirk, mirrored: None is not special-cased, so clearing
        # the action raises ValueError — a rule without action is invalid.
        if not isinstance(value, Action):
            value = Action(value)
        self._action = value

    @property
    def proto(self):
        '''protocol to match'''
        return self._proto

    @proto.setter
    def proto(self, value):
        if value is not None and not isinstance(value, Proto):
            value = Proto(value)
        # Upstream dependency clearing, mirrored: setting proto to anything
        # but tcp/udp clears dstports; to anything but icmp clears icmptype.
        if value not in ('tcp', 'udp'):
            self.dstports = None
        if value not in ('icmp',):
            self.icmptype = None
        self._proto = value

    @property
    def dsthost(self):
        '''destination host/network'''
        return self._dsthost

    @dsthost.setter
    def dsthost(self, value):
        if value is not None and not isinstance(value, DstHost):
            value = DstHost(value)
        self._dsthost = value

    @property
    def dstports(self):
        '''Destination port(s) (for 'tcp' and 'udp' protocol only)'''
        return self._dstports

    @dstports.setter
    def dstports(self, value):
        if value is not None:
            if self.proto not in ('tcp', 'udp'):
                raise ValueError(
                    "dstports valid only for 'tcp' and 'udp' protocols")
            if not isinstance(value, DstPorts):
                value = DstPorts(value)
        self._dstports = value

    @property
    def icmptype(self):
        '''ICMP packet type (for 'icmp' protocol only)'''
        return self._icmptype

    @icmptype.setter
    def icmptype(self, value):
        if value is not None:
            if self.proto not in ('icmp',):
                raise ValueError("icmptype valid only for 'icmp' protocol")
            if not isinstance(value, IcmpType):
                value = IcmpType(value)
        self._icmptype = value

    @property
    def specialtarget(self):
        '''Special target, for now only 'dns' supported'''
        return self._specialtarget

    @specialtarget.setter
    def specialtarget(self, value):
        # Upstream quirk, mirrored: None is not special-cased, so
        # `rule.specialtarget = None` raises ValueError(None) instead of
        # clearing. The UI clears elements by rebuilding the rule.
        if not isinstance(value, SpecialTarget):
            value = SpecialTarget(value)
        self._specialtarget = value

    @property
    def expire(self):
        '''Timestamp (UNIX epoch) on which this rule expires'''
        return self._expire

    @expire.setter
    def expire(self, value):
        if not isinstance(value, Expire):
            if isinstance(value, str) and value.startswith('+'):
                # '+seconds' relative expire, as accepted by the qvm-firewall
                # CLI: resolve to absolute epoch seconds at construction
                # time (deviation #2 in the module docstring). Garbage after
                # '+' raises ValueError from int(), as in the CLI.
                value = str(int(time.time()) + int(value[1:]))
            # Upstream quirk, mirrored: expire=None raises TypeError from
            # int(None) — None is not special-cased.
            value = Expire(value)
        self._expire = value

    @property
    def comment(self):
        '''User comment'''
        return self._comment

    @comment.setter
    def comment(self, value):
        if not isinstance(value, Comment):
            value = Comment(value)
        self._comment = value

    @property
    def rule(self):
        '''API representation of this rule — exactly the line the Admin API
        expects, elements in RULE_SERIALIZE_ORDER, comment last, joined with
        single spaces. Elements whose API representation is None (the /0
        "any host" case) are omitted.'''
        values = []
        # comment must be the last one
        for prop in RULE_SERIALIZE_ORDER:
            value = getattr(self, prop)
            if value is None:
                continue
            if value.rule is None:
                continue
            values.append(value.rule)
        return ' '.join(values)

    def __eq__(self, other):
        if isinstance(other, Rule):
            return self.rule == other.rule
        if isinstance(other, str):
            # Deviation #1: upstream compares against the `str` builtin
            # (a typo, always False). Comparing serialized forms is what
            # makes delete-by-match work with plain API strings.
            return self.rule == other
        return NotImplemented

    def __repr__(self):
        return 'Rule(\'{}\')'.format(self.rule)


class RuleList:
    '''The ordered firewall rule list. Index IS evaluation order.

    Models the qvm-firewall operations on ``vm.firewall.rules``:

    ==========================  =============================
    CLI                         RuleList
    ==========================  =============================
    ``add <match>``             :meth:`append`
    ``add --before N <match>``  :meth:`insert_before`
    ``del --rule-no N``         :meth:`delete_by_index`
    ``del <match>``             :meth:`delete_by_match`
    ``list`` (numbers from 0)   indexing, ``len()``, iteration
    (grid only)                 :meth:`move_up` / :meth:`move_down`
    ==========================  =============================

    Indexes are strict (deviation #3): zero-based, non-negative,
    bounds-checked — ``IndexError`` otherwise. ``move_up(0)`` and
    ``move_down(len-1)`` are no-ops returning the unchanged index: there is
    no neighbor to swap with, and the UI greys those buttons out.

    Rules are accepted as :class:`Rule` objects or API-form strings.
    '''

    def __init__(self, rules=()):
        self._rules = [self._coerce(rule) for rule in rules]

    @staticmethod
    def _coerce(rule):
        if isinstance(rule, Rule):
            return rule
        if isinstance(rule, str):
            return Rule(rule)
        raise TypeError(
            'expected Rule or API-form string, got {}'.format(
                type(rule).__name__))

    def _check_index(self, index, allow_len=False):
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError('rule index must be an int, got {!r}'.format(
                index))
        limit = len(self._rules) if allow_len else len(self._rules) - 1
        if index < 0 or index > limit:
            raise IndexError(
                'rule index {} out of range, the list holds {} rule(s) '
                '(indexes are 0-based evaluation order)'.format(
                    index, len(self._rules)))
        return index

    @classmethod
    def from_api_text(cls, text):
        '''Parse what ``admin.vm.firewall.Get`` returns: one API-form rule
        per line. Mirrors ``Firewall.load_rules`` — no line is skipped, so a
        blank line is a parse error (``missing action=``), as upstream.'''
        return cls(Rule(rule_str) for rule_str in text.splitlines())

    def to_api_text(self):
        '''Serialize exactly the payload ``Firewall.save_rules`` sends to
        ``admin.vm.firewall.Set``: every rule on its own line, trailing
        newline included (ASCII-encodable, as upstream encodes it).'''
        return ''.join('{}\n'.format(rule.rule) for rule in self._rules)

    @property
    def rules(self):
        '''A copy of the ordered rules. Mutate through the explicit methods
        only — editing a returned list does not change evaluation order.'''
        return list(self._rules)

    def append(self, rule):
        '''Add at the end (``qvm-firewall add``). Returns the new index.'''
        self._rules.append(self._coerce(rule))
        return len(self._rules) - 1

    def insert_before(self, index, rule):
        '''Insert so the new rule takes evaluation slot ``index``, pushing
        the current occupant and everything after it down by one
        (``qvm-firewall add --before N``). Returns ``index``.'''
        self._check_index(index, allow_len=True)
        self._rules.insert(index, self._coerce(rule))
        return index

    def delete_by_index(self, index):
        '''Remove and return the rule at ``index``
        (``qvm-firewall del --rule-no N``).'''
        self._check_index(index)
        return self._rules.pop(index)

    def delete_by_match(self, match):
        '''Remove the FIRST rule equal to ``match`` and return
        ``(index, rule)`` (``qvm-firewall del <match>`` is ``list.remove``:
        first match only). ``ValueError`` if nothing matches. Duplicates
        beyond the first are left alone — that is upstream semantics, and
        de-duplication is safety.py's job to flag, not to perform.'''
        match = self._coerce(match)
        for index, rule in enumerate(self._rules):
            if rule == match:
                del self._rules[index]
                return index, rule
        raise ValueError('no rule matches {!r}'.format(match.rule))

    def move_up(self, index):
        '''Swap the rule at ``index`` with its predecessor. Returns the new
        index. No-op at the top (returns 0).'''
        self._check_index(index)
        if index == 0:
            return 0
        self._rules[index - 1], self._rules[index] = \
            self._rules[index], self._rules[index - 1]
        return index - 1

    def move_down(self, index):
        '''Swap the rule at ``index`` with its successor. Returns the new
        index. No-op at the bottom.'''
        self._check_index(index)
        if index == len(self._rules) - 1:
            return index
        self._rules[index], self._rules[index + 1] = \
            self._rules[index + 1], self._rules[index]
        return index + 1

    def __len__(self):
        return len(self._rules)

    def __iter__(self):
        return iter(list(self._rules))

    def __getitem__(self, index):
        # Strict on purpose: no negative indexing, no slices. The grid shows
        # exactly these indexes; nothing may address a rule implicitly.
        self._check_index(index)
        return self._rules[index]

    def __eq__(self, other):
        if isinstance(other, RuleList):
            return self._rules == other._rules
        return NotImplemented

    def __repr__(self):
        return 'RuleList({!r})'.format([rule.rule for rule in self._rules])
