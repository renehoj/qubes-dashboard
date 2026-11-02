# Copied unchanged from qvm-firewall-ui v1.0.0 (qvm_firewall_ui/validate.py, commit 9ab90bb).
# Keep it in step with that file rather than editing it here.
# -*- coding: utf-8 -*-
'''Engine acceptance rules, mirrored. Standard library only (plus .rules).

``rules.py`` mirrors the CLIENT model (``qubesadmin.firewall``) — what the
qvm-firewall tool builds in memory. This module mirrors what the whole
``qvm-firewall`` pipeline ACCEPTS end to end, so the UI rejects a rule exactly
when typing it into ``qvm-firewall`` would fail somewhere along the way.

Three layers decide acceptance, and they are not identical:

CLIENT  ``qubesadmin/firewall.py`` (qubes-core-admin-client main, fetched
        2026-10-15, sha256
        9e67c1f4cdebad79307df53724e558566927dfdda42e9eba17d0e028bfae170d)
        — mirrored 1:1 by rules.py, quirks included.
CLI     ``qubesadmin/tools/qvm_firewall.py`` (same source, sha256
        93919833923eb32da659267da42509282b7b5df6c64cb35fa28e5f2187057467)
        — ``RuleAction``: rejects empty ``key=`` values (a token ending in
        '='), rejects unknown keywords, resolves ``+seconds`` expire.
SERVER  ``qubes/firewall.py`` (qubes-core-admin main, fetched 2026-10-15,
        sha256 df4eb508d459c62adfca8a5663a702d934be46932aa02723e59d16248ccc45db)
        — the engine proper. qubesd re-parses every line of an
        ``admin.vm.firewall.Set`` payload through ``Rule.from_api_string``,
        so a rule the SERVER rejects makes ``save_rules()`` (and therefore
        ``qvm-firewall add``) fail even though the CLIENT built it happily.

Decision rule implemented here: a value/line/rule is VALID only if all three
layers accept it — the intersection. Where the layers disagree, the stricter
verdict wins, because that is what the user experiences when they run
``qvm-firewall``. The disagreements that matter (all have named tests):

- ports: CLIENT accepts 0..65536, SERVER accepts 0..65535, this module
  enforces **1..65535** (T1). The card for this module demands 1-65535; port
  0 is reserved and cannot be connected to, so a ``dstports=0`` rule is a
  user error the UI must not paper over.
- ``icmptype``: CLIENT accepts anything ``int()`` parses ('0008', ' 8', '+8',
  '8_0'); SERVER requires ``str.isdigit()`` and at most 3 characters
  ('bad ICMP type'). Intersection: SERVER's format, both layers' 0..255.
- ``expire``: CLIENT accepts negative values ('-5'); SERVER requires digits
  only ('bad expiration time'). ``+seconds`` is accepted at the field level
  (the CLI resolves it before the model sees it) but rejected inside a raw
  API line (SERVER charset: '+' is not a safe character).
- ``comment``: CLIENT accepts arbitrary text; SERVER restricts the charset to
  ``[A-Za-z0-9:;,./-_[] ]`` ('strange characters comment') — notably '=' is
  rejected by the engine even though the client model serializes it fine.
- duplicate options in one line: CLIENT collapses them (last wins); SERVER
  rejects ("Option 'x' already set"). Intersection: reject.
- double spaces in a raw API line: CLIENT collapses; SERVER's
  ``strip().split(' ')`` chokes on the empty token. Intersection: reject.
- ``dstname=``: SERVER accepts it as a dsthost synonym; CLIENT cannot parse
  it back ('Unknown rule elements'). Rejected here (T3) — the UI must never
  accept a rule its own model cannot round-trip.
- empty ``dsthost``: SERVER's charset check passes vacuously on '' (an
  ``all()`` quirk); the CLI rejects ``dsthost=`` outright (token ends in
  '='). Rejected here (T2) with SERVER's own message for None.

Message policy: where the engine raises a clean sentence, this module returns
that sentence VERBATIM (constants below cite qubes/firewall.py lines, so a
reviewer can grep them against upstream). Where the engine's failure is an
implementation byproduct (a bare ``ValueError(value)`` from RuleChoice, an
``int()`` unpack traceback), the message is UI-grade text of ours — the
accept/reject DECISION always matches the engine; only the wording is ours.

Public API:

- ``check_<field>(value) -> str | None`` — per-field verdicts for the dialog
  (None = accepted, str = the message to show). ``CHECKERS`` maps field name
  to checker. ``check_expire_api`` is the wire-level expire variant:
  '+seconds' is an INPUT form only and never appears in a raw API line —
  qubesd's charset rejects the '+'.
- ``validate_fields(**fields) -> [Problem]`` — dialog-level: validate raw
  field values BEFORE constructing a ``rules.Rule``, dependencies included,
  so the UI never shows a constructor traceback. ``None`` = field absent.
- ``validate_rule(rule) -> [Problem]`` — pre-save gate for a constructed
  ``rules.Rule`` (or an API-form string, which delegates to
  ``validate_api_line``). Catches everything the CLIENT mirror lets through
  that the SERVER would reject at ``save_rules()`` time.
- ``validate_api_line(line) -> [Problem]`` — full mirror of SERVER's
  ``Rule.from_api_string`` plus the CLI's input rules: token structure,
  duplicates, unknown options, then per-field checks.
- ``validate_api_text(text) -> [Problem]`` / ``validate_rule_list(rules) ->
  [Problem]`` — the same, per line / per rule, with ``Problem.index`` set.
- ``resolve_expire(value) -> int`` — the CLI's ``+seconds`` resolution.

Flow the UI is expected to use (see card t_419e57b1): dialog fields ->
``validate_fields`` -> construct via ``rules.Rule`` -> ``validate_rule`` ->
``safety.analyze`` -> snapshot -> save. Validation never raises on bad USER
input; it collects Problems. It raises only on programmer error (a Rule
where a string was promised is fine, but an unknown field keyword is a
TypeError).
'''

import datetime
import socket
import string
import time

from collections import namedtuple

from .rules import Rule, RuleList

__all__ = [
    'Problem', 'FIELDS', 'CHECKERS',
    'check_action', 'check_proto', 'check_dsthost', 'check_dstports',
    'check_icmptype', 'check_specialtarget', 'check_expire',
    'check_expire_api', 'check_comment',
    'resolve_expire', 'validate_fields', 'validate_rule',
    'validate_api_line', 'validate_api_text', 'validate_rule_list',
]

#: Rule element names the engine knows (qubes/firewall.py Rule properties).
FIELDS = ('action', 'proto', 'dsthost', 'dstports', 'icmptype',
          'specialtarget', 'expire', 'comment')

# --- engine messages, verbatim (cite: qubes/firewall.py, fetched 2026-10-15)
M_STRANGE_CHARS = 'strange characters in rule'            # RuleOption, :44
M_STRANGE_COMMENT = 'strange characters comment'          # Comment, :255
M_EMPTY_DSTHOST = 'Destination address cannot be empty'   # DstHost, :103
M_BAD_PREFIXLEN = 'bad prefix length'                     # DstHost, :143
M_NETMASK_NONNEG = 'netmask must be non-negative'         # DstHost, :146
M_V6_BETWEEN = 'netmask for IPv6 must be between 0 and 128'   # :113-115
M_V4_BETWEEN = 'netmask for IPv4 must be between 0 and 32'    # :128-130
M_V6_MAX = 'netmask for IPv6 must be <= 128'              # DstHost, :152
M_V4_MAX = 'netmask for IPv4 must be <= 32'               # DstHost, :158
M_DOTS = 'Invalid number of dots in IPv4 address'         # DstHost, :122-124
M_INVALID_HOSTNAME = 'Invalid hostname'                   # DstHost, :138
M_PORTS_RANGE = 'Ports out of range'                      # DstPorts, :186
M_PORT_ORDER = 'Invalid port range'                       # DstPorts, :188
M_BAD_ICMP = 'bad ICMP type'                              # IcmpType, :205
M_ICMP_RANGE = 'ICMP type out of range'                   # IcmpType, :208
M_BAD_EXPIRE = 'bad expiration time'                      # Expire, :231
M_DSTPORTS_PROTO = "dstports valid only for 'tcp' and 'udp' protocols"  # :342
M_ICMPTYPE_PROTO = "icmptype valid only for 'icmp' protocol"            # :351
# Missing action: SERVER enforces it via property_require('action') (:286),
# whose message lives in the qubes property system (not available off-box);
# CLIENT's message is used — the verdict is identical in both layers.
M_MISSING_ACTION = 'missing action='
M_UNKNOWN_OPTION = 'Unknown firewall option'              # Rule, :476
M_OPTION_ALREADY_SET = "Option '{}' already set"          # Rule, :462-463

# --- our messages, where the engine's is an implementation byproduct
M_INVALID_IP = 'Invalid IP address: '          # SERVER :166, verbatim + host
M_TOO_MANY_SLASH = 'Too many /: '              # SERVER :105, verbatim + value
M_BAD_CHOICE = "invalid {field}: {value!r} (accepted: {allowed})"
M_BAD_PORT_SPEC = 'invalid port specification: {!r} (a port or lo-hi, e.g. 443 or 1200-1400)'
M_BAD_EXPIRE_REL = "invalid relative expire: {!r} ('+seconds', e.g. +300)"
M_EXPIRE_UNREPRESENTABLE = 'expiration time out of representable range: {!r}'
M_EMPTY_ELEMENT = 'empty rule element (a line is key=value tokens separated by single spaces)'
M_ELEMENT_WITHOUT_EQUALS = "rule element without '=': {!r}"

# --- charsets, verbatim from the engine
# SERVER RuleOption base: every option value passes through this set (:41).
_OPTION_SAFE_SET = frozenset(string.ascii_letters + string.digits
                             + ':;,./-_[]')
# SERVER Comment: the same set plus space (:252). '=' is NOT in it — the
# engine rejects a comment containing '=' even though CLIENT serializes one.
_COMMENT_SAFE_SET = _OPTION_SAFE_SET | frozenset(' ')
# SERVER DstHost hostname branch: lowercase only (:136).
_HOSTNAME_SAFE_SET = frozenset(string.ascii_lowercase + string.digits
                               + '-._')

# Port bounds enforced here (T1 in the module docstring): the card for this
# module demands 1-65535. For the record: CLIENT accepts 0..65536
# ('port < 0 or port > 65536'), SERVER accepts 0..65535
# ('port < 0 or port >= 65536').
PORT_MIN = 1
PORT_MAX = 65535


_ProblemBase = namedtuple('Problem', ('field', 'value', 'message', 'index'))


class Problem(_ProblemBase):
    '''One validation finding: (field, value, message, index).

    ``field`` is the rule element the problem belongs to, or None for
    line-structure problems. ``index`` is None for single-rule/single-line
    validation and the rule/line number inside list-level validation.
    A namedtuple so callers can unpack it; equality is tuple equality.'''

    __slots__ = ()

    def __new__(cls, field, value, message, index=None):
        return _ProblemBase.__new__(cls, field, value, message, index)


def _in_set(value, safe_set):
    return all(c in safe_set for c in value)


def _is_ipv4(value):
    try:
        socket.inet_pton(socket.AF_INET, value)
        return True
    except (socket.error, ValueError):
        return False


def _is_ipv6(value):
    try:
        socket.inet_pton(socket.AF_INET6, value)
        return True
    except (socket.error, ValueError):
        return False


def _choice_problem(field, value, allowed):
    # SERVER RuleChoice raises ValueError(value) — a bare value, not a
    # sentence. Message policy: ours, decision the engine's.
    return M_BAD_CHOICE.format(field=field, value=value,
                               allowed=', '.join(allowed))


def check_action(value):
    '''action ∈ {accept, drop}, case-sensitive (SERVER :79-85).

    Charset first, membership second — SERVER RuleChoice calls the base
    RuleOption charset check before the allowed-values check, so 'acc ept'
    fails as 'strange characters in rule', not as an unknown action.'''
    value = str(value)
    if not _in_set(value, _OPTION_SAFE_SET):
        return M_STRANGE_CHARS
    if value not in ('accept', 'drop'):
        return _choice_problem('action', value, ('accept', 'drop'))
    return None


def check_proto(value):
    '''proto ∈ {tcp, udp, icmp} (SERVER :88-95).'''
    value = str(value)
    if not _in_set(value, _OPTION_SAFE_SET):
        return M_STRANGE_CHARS
    if value not in ('tcp', 'udp', 'icmp'):
        return _choice_problem('proto', value, ('tcp', 'udp', 'icmp'))
    return None


def check_specialtarget(value):
    '''specialtarget: exactly 'dns', nothing else (SERVER :216-221).'''
    value = str(value)
    if not _in_set(value, _OPTION_SAFE_SET):
        return M_STRANGE_CHARS
    if value != 'dns':
        return _choice_problem('specialtarget', value, ('dns',))
    return None


def check_dsthost(value):
    '''dsthost: IPv4, IPv6, or DNS name, with optional /prefix.

    Mirrors SERVER DstHost (:98-172) branch for branch:

    - more than one '/' -> 'Too many /: <value>';
    - bare IPv6 -> gains /128; bare IPv4 -> exactly 3 dots, gains /32;
    - bare non-IP -> hostname, charset [a-z0-9-._] lowercase only
      ('Invalid hostname'). Note the engine quirks this mirrors: '' passes
      the charset vacuously (rejected here anyway, T2), and a malformed IP
      like '1.2.3' or '01.2.3.4' falls through to the hostname branch and is
      ACCEPTED as a hostname — digits and dots are hostname-legal;
    - with '/': the prefix must be ``str.isdigit()`` ('bad prefix length' —
      SERVER :142-143; CLIENT's int() would also take '+8'/'-3', the
      intersection does not), IPv6 prefix <= 128, IPv4 prefix <= 32 and
      exactly 3 dots, and a hostname with a prefix is rejected
      ('Invalid IP address: <host>') — prefixes are IP-only;
    - the dot-count check ('Invalid number of dots in IPv4 address') is
      mirrored at the exact upstream position, after a successful
      inet_pton(AF_INET) — where it is unreachable by construction, because
      inet_pton only succeeds on exactly four octets. It is kept (and its
      message constant exported) because fidelity to the engine source is
      the contract; the REACHABLE sibling behaviors are tested: bare
      malformed IP -> hostname quirk, prefixed malformed IP -> 'Invalid IP
      address'.
    '''
    value = str(value)
    if not value:
        # T2: SERVER's None message; the empty string itself would pass both
        # models' charset checks vacuously, but the CLI rejects 'dsthost='
        # (token ends in '=') and an empty host rule is meaningless.
        return M_EMPTY_DSTHOST
    if value.count('/') > 1:
        return M_TOO_MANY_SLASH + value
    if '/' not in value:
        if _is_ipv6(value):
            return None  # gains /128; no prefix bounds to check on a string
        if _is_ipv4(value):
            if value.count('.') != 3:  # defensive, mirrors SERVER :121-124
                return M_DOTS
            return None  # gains /32
        if not _in_set(value, _HOSTNAME_SAFE_SET):
            return M_INVALID_HOSTNAME
        return None
    host, _, prefix = value.partition('/')
    if not prefix.isdigit():
        return M_BAD_PREFIXLEN
    prefixlen = int(prefix)
    if prefixlen < 0:  # defensive, mirrors SERVER :145-146 (isdigit() first)
        return M_NETMASK_NONNEG
    if _is_ipv6(host):
        if prefixlen > 128:
            return M_V6_MAX
    elif _is_ipv4(host):
        if prefixlen > 32:
            return M_V4_MAX
        if host.count('.') != 3:  # defensive, mirrors SERVER :160-163
            return M_DOTS
    else:
        return M_INVALID_IP + host
    # SERVER runs the base charset last (super().__init__ after the branch);
    # reachable for exotic isdigit() characters (e.g. non-ASCII digits).
    if not _in_set(value, _OPTION_SAFE_SET):
        return M_STRANGE_CHARS
    return None


def check_dstports(value):
    '''dstports: a port or a lo-hi range, PORT_MIN..PORT_MAX, lo <= hi.

    Format parsing follows the engine's int() semantics (SERVER :176-188,
    CLIENT identical): one '-' separates lo/hi, more than one is a
    ValueError, and each side goes through int() — which is why the engine
    (both layers!) also accepts '+80', ' 80' and even '8_0'. Those quirks
    are mirrored, not fixed: the decision must match what save_rules() would
    survive. Bounds are T1 (1..65535, stricter than both layers, see module
    docstring); the message is SERVER's ('Ports out of range', :186).'''
    value = str(value)
    parts = value.split('-')
    if len(parts) > 2:
        # SERVER raises ValueError(value) — a bare value. Ours is a sentence.
        return M_BAD_PORT_SPEC.format(value)
    try:
        ports = [int(part) for part in parts]
    except ValueError:
        # SERVER: int() raises ('invalid literal...'); same verdict, our text.
        return M_BAD_PORT_SPEC.format(value)
    if any(port < PORT_MIN or port > PORT_MAX for port in ports):
        return M_PORTS_RANGE
    if ports[0] > ports[-1]:
        return M_PORT_ORDER
    return None


def check_icmptype(value):
    '''icmptype: SERVER format (str.isdigit(), at most 3 chars — 'bad ICMP
    type', :202-205) and both layers' range 0..255 ('ICMP type out of
    range', :207-208).

    An int instance bypasses the format check, exactly as SERVER
    (isinstance(int) short-circuit, :202). CLIENT's laxer int() acceptance
    ('0008', ' 8', '+8') is NOT mirrored — those die at save_rules() with
    'bad ICMP type', so the intersection rejects them here.'''
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
        raw = str(value)
    else:
        raw = str(value)
        if len(raw) > 3 or not raw.isdigit():
            return M_BAD_ICMP
        number = int(raw)
    if number < 0 or number > 255:
        return M_ICMP_RANGE
    if not _in_set(raw, _OPTION_SAFE_SET):
        # SERVER runs the base charset on the ORIGINAL value: exotic
        # isdigit() characters (non-ASCII digits) fail here, not above.
        return M_STRANGE_CHARS
    return None


def _representable(timestamp):
    '''Would datetime.fromtimestamp accept this epoch value? SERVER uses
    naive local fromtimestamp; UTC is used here so the verdict is
    machine-independent. Only extreme magnitudes are affected by the
    difference, and those fail on every platform.'''
    try:
        datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        return True
    except (OverflowError, OSError, ValueError):
        return False


def resolve_expire(value):
    '''Parse an expire value into absolute UNIX seconds.

    Accepts what the pipeline accepts: an int/float (SERVER Expire
    isinstance check, :227-230), a digits-only string (SERVER
    ``untrusted_value.isdigit()``, else 'bad expiration time'), or the CLI's
    '+seconds' relative form (qvm_firewall.py RuleAction :66-69 —
    ``int(value[1:])`` added to now; garbage after '+' is a ValueError
    there, and here). Raises ValueError with the engine's message (or ours
    where the engine's is a byproduct). The resolved value must be
    representable and non-negative (a '+N' resolving below zero would
    serialize with a '-' and fail SERVER's isdigit check).'''
    if isinstance(value, bool):
        raise ValueError(M_BAD_EXPIRE)
    if isinstance(value, (int, float)):
        timestamp = int(value)
    else:
        value = str(value)
        if value.startswith('+'):
            try:
                delta = int(value[1:])
            except ValueError:
                raise ValueError(M_BAD_EXPIRE_REL.format(value))
            timestamp = int(time.time()) + delta
            if timestamp < 0:
                raise ValueError(M_BAD_EXPIRE)
        elif value.isdigit():
            timestamp = int(value)
        else:
            raise ValueError(M_BAD_EXPIRE)
    if not _representable(timestamp):
        raise ValueError(M_EXPIRE_UNREPRESENTABLE.format(value))
    return timestamp


def check_expire(value):
    '''expire as typed into the UI / qvm-firewall: absolute UNIX seconds
    (digits only) or '+seconds' relative (CLI form, qvm_firewall.py:66-69).

    '+seconds' is an INPUT form only: the CLI and rules.Rule both resolve it
    to absolute digits before the model serializes, so the engine never sees
    a '+'. That is why this checker accepts it while
    :func:`check_expire_api` (the wire-level mirror used by
    validate_api_line) rejects it — SERVER's base charset has no '+', and
    a raw API line carrying 'expire=+300' fails in qubesd with 'strange
    characters in rule'.'''
    try:
        resolve_expire(value)
    except ValueError as err:
        return str(err)
    return None


def check_expire_api(value):
    '''expire as it appears in a raw Admin-API line: SERVER Expire verbatim
    (:224-232), including the check ORDER — the base charset runs first
    (super().__init__), so '+300' fails as 'strange characters in rule'
    while 'abc' or '-5' or '123.5' pass the charset and fail the
    ``isdigit()`` check as 'bad expiration time'. int/float instances
    bypass both, as in SERVER.'''
    if isinstance(value, bool):
        return M_BAD_EXPIRE
    if isinstance(value, (int, float)):
        if not _representable(int(value)):
            return M_EXPIRE_UNREPRESENTABLE.format(value)
        return None
    value = str(value)
    if not _in_set(value, _OPTION_SAFE_SET):
        return M_STRANGE_CHARS
    if not value.isdigit():
        return M_BAD_EXPIRE
    if not _representable(int(value)):
        return M_EXPIRE_UNREPRESENTABLE.format(value)
    return None


def check_comment(value):
    '''comment: free text within SERVER's charset [A-Za-z0-9:;,./-_[] plus
    space] (:247-256, 'strange characters comment').

    This is the divergence that bites: CLIENT's Comment accepts ANY text and
    rules.py serializes it happily, but qubesd rejects '=' , '!', '?' etc. at
    save_rules() time. The empty comment is accepted here and dropped at
    serialization (CLIENT quirk, named in rules.py's tests).'''
    value = str(value)
    if not _in_set(value, _COMMENT_SAFE_SET):
        return M_STRANGE_COMMENT
    return None


#: field name -> checker, in RULE_PARSE_ORDER-ish order (action first).
#: These are the INPUT-level checkers (dialog fields / qvm-firewall
#: arguments): expire accepts the CLI's '+seconds' form.
CHECKERS = {
    'action': check_action,
    'proto': check_proto,
    'dsthost': check_dsthost,
    'dstports': check_dstports,
    'icmptype': check_icmptype,
    'specialtarget': check_specialtarget,
    'expire': check_expire,
    'comment': check_comment,
}

#: WIRE-level checkers for raw Admin-API lines (validate_api_line): same as
#: CHECKERS except expire, where '+seconds' does not exist — it is resolved
#: before serialization, and qubesd's charset rejects the '+'.
_LINE_CHECKERS = dict(CHECKERS, expire=check_expire_api)


def _dependency_problems(get):
    '''Cross-field engine dependencies, given a ``get(field)`` accessor.

    SERVER enforces both at Rule construction (:277-286, :338-351), CLIENT
    in its setters — identical verdicts and messages:

    - dstports requires proto ∈ {tcp, udp};
    - icmptype requires proto == icmp.

    A missing proto fails both dependencies, which is the engine behavior
    (proto None is not in the allowed sets).'''
    problems = []
    proto = get('proto')
    if get('dstports') is not None and str(proto) not in ('tcp', 'udp'):
        problems.append(
            Problem('dstports', get('dstports'), M_DSTPORTS_PROTO))
    if get('icmptype') is not None and str(proto) != 'icmp':
        problems.append(
            Problem('icmptype', get('icmptype'), M_ICMPTYPE_PROTO))
    return problems


def validate_fields(**fields):
    '''Validate raw dialog field values before any Rule is constructed.

    Keyword names are FIELDS; anything else is a TypeError (programmer
    error). ``None`` means the field is absent/cleared and is skipped — the
    UI must pass None, not '', for fields it disabled (rules.py mirrors the
    upstream quirk that setting action/specialtarget/expire to None raises).
    Returns every Problem found: missing action, per-field engine verdicts,
    and the two proto dependencies. An empty list means rules.Rule(**fields)
    will construct AND qubesd will accept the serialized line.'''
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise TypeError('unknown field(s): {}'.format(sorted(unknown)))
    problems = []
    if fields.get('action') is None:
        problems.append(Problem('action', None, M_MISSING_ACTION))
    for field in FIELDS:
        value = fields.get(field)
        if value is None:
            continue
        message = CHECKERS[field](value)
        if message is not None:
            problems.append(Problem(field, value, message))
    problems.extend(_dependency_problems(fields.get))
    return problems


def validate_rule(rule):
    '''Pre-save gate for one constructed rule.

    Accepts a ``rules.Rule`` or an API-form string (delegated to
    ``validate_api_line``). Every present element is re-checked against the
    engine checkers using the value the model STORES — which is how the
    client-looser-than-engine divergences get caught on already-constructed
    rules: dstports 0/65536, icmptype '0008', expire '-5', a comment with
    '='. Dependencies are re-checked for robustness (rules.Rule enforces
    them at construction; a hand-assembled object might not).'''
    if isinstance(rule, str):
        return validate_api_line(rule)
    if not isinstance(rule, Rule):
        raise TypeError('expected rules.Rule or API-form string, got {}'
                        .format(type(rule).__name__))
    problems = []
    for field in FIELDS:
        element = getattr(rule, field)
        if element is None:
            continue
        message = CHECKERS[field](str(element))
        if message is not None:
            problems.append(Problem(field, str(element), message))
    problems.extend(_dependency_problems(
        lambda name: getattr(rule, name, None)))
    return problems


def validate_api_line(line):
    '''Validate one raw Admin-API-form line the way the engine parses it.

    Mirrors SERVER ``Rule.from_api_string`` (:446-478) token by token, plus
    the intersection rules from the module docstring:

    - ``comment=`` partitions the line FIRST; everything after the first
      'comment=' is the comment text (both layers); an empty comment is
      dropped, not an error (CLIENT quirk, SERVER ``if untrusted_comment:``);
    - the options part is ``strip().split(' ')`` — SERVER's exact call, so a
      double space yields an empty token and is REJECTED (CLIENT would
      collapse it; intersection verdict), as is a token without '=';
    - dst4/dst6 are dsthost synonyms; dstname is rejected (T3);
    - a duplicate option is rejected with SERVER's message
      ("Option 'x' already set"; a synonym collision reports 'dsthost',
      as SERVER :470-473 does);
    - unknown keys: 'Unknown firewall option' (SERVER :476);
    - then per-field checkers and the two proto dependencies.

    Returns [] exactly when qubesd would accept the line.'''
    if not isinstance(line, str):
        raise TypeError('expected an API-form string, got {}'
                        .format(type(line).__name__))
    problems = []
    options, sep, comment = line.partition('comment=')
    if sep and comment:
        message = check_comment(comment)
        if message is not None:
            problems.append(Problem('comment', comment, message))
    # SERVER: untrusted_options.strip().split(' ') — unconditionally, so an
    # empty options part is one empty token and fails the unpack.
    seen = {}
    for token in options.strip().split(' '):
        if not token:
            problems.append(Problem(None, token, M_EMPTY_ELEMENT))
            continue
        if '=' not in token:
            problems.append(
                Problem(None, token, M_ELEMENT_WITHOUT_EQUALS.format(token)))
            continue
        key, value = token.split('=', 1)
        if key == 'dstname':
            # T3: SERVER accepts it, CLIENT cannot re-parse it.
            problems.append(Problem(key, value, M_UNKNOWN_OPTION))
            continue
        field = 'dsthost' if key in ('dst4', 'dst6') else key
        if field not in FIELDS:
            problems.append(Problem(key, value, M_UNKNOWN_OPTION))
            continue
        if field in seen:
            problems.append(
                Problem(field, value, M_OPTION_ALREADY_SET.format(field)))
            continue
        seen[field] = value
        message = _LINE_CHECKERS[field](value)
        if message is not None:
            problems.append(Problem(field, value, message))
    if 'action' not in seen:
        problems.append(Problem('action', None, M_MISSING_ACTION))
    problems.extend(_dependency_problems(seen.get))
    return problems


def validate_api_text(text):
    '''Validate a whole admin.vm.firewall payload (one rule per line).

    Mirrors ``RuleList.from_api_text``'s line split: no line is skipped, so
    a blank line yields problems at its index ('missing action=' plus the
    empty-token finding), exactly as the engine fails on it. A trailing
    newline does not create a phantom blank line (splitlines semantics —
    what ``to_api_text`` produces must validate clean).'''
    if not isinstance(text, str):
        raise TypeError('expected API text, got {}'.format(
            type(text).__name__))
    problems = []
    for index, line in enumerate(text.splitlines()):
        for problem in validate_api_line(line):
            problems.append(problem._replace(index=index))
    return problems


def validate_rule_list(rules):
    '''Validate an ordered rule collection pre-save; Problem.index is set.

    Accepts a RuleList or any iterable of Rule/API-form strings (coerced
    through RuleList, so unparseable garbage raises ValueError — raw text
    goes through validate_api_text instead, which reports instead of
    raising).'''
    if not isinstance(rules, RuleList):
        rules = RuleList(rules)
    problems = []
    for index, rule in enumerate(rules):
        for problem in validate_rule(rule):
            problems.append(problem._replace(index=index))
    return problems
