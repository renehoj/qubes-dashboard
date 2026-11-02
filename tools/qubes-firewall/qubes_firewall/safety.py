# Copied unchanged from qvm-firewall-ui v1.0.0 (qvm_firewall_ui/safety.py, commit 9ab90bb).
# Keep it in step with that file rather than editing it here.
# -*- coding: utf-8 -*-
'''Lockout / shadowing / expiry analysis of a whole ordered rule list.
Standard library only (plus .rules).

Runs BEFORE anything is written (DESIGN.md §5): the UI must call
``analyze(rules, netvm_addrs=...)`` on the complete ordered list, show every
warning, and require explicit confirmation — none is silently swallowed
(card t_419e57b1). Five detectors, exactly the five the card names:

1. ``detect_netvm_denied``    — a rule that denies the qube's own netvm;
2. ``detect_dns_denied``      — a rule that denies DNS while LATER rules
   depend on name resolution;
3. ``detect_accept_all_shadow`` — an accept-all early in the list,
   shadowing every rule after it;
4. ``detect_expired``         — rules whose expire time has already passed;
5. ``detect_duplicates``      — exact duplicates.

Every detector has named tests, and every detector is mutation-checked: the
evidence bundle proves that stubbing a detector out makes its tests fail
(an untested detector is not a detector).

Design notes that decide edge behavior (each has a named test):

- **Expired rules are inert.** The engine skips expired rules when it
  applies or re-saves them (qubes/firewall.py: ``Rule.rule``/``api_rule``
  return None when ``expire.expired``, ``qdb_entries`` and ``save()`` skip
  them). Detectors 1-3 therefore ignore expired rules — a shadowing
  accept-all that expired an hour ago shadows nothing. Detector 4 reports
  them precisely because the user may still believe they apply. Detector 5
  (duplicates) is structural and reports regardless of expiry.
- **Matching uses the serialized view.** rules.py mirrors the client model,
  where ``dst4=0.0.0.0/0`` normalizes to "no dsthost at all" (any host is
  expressed by absence). A drop rule with dsthost 0.0.0.0/0 is therefore a
  BARE drop — total lockout — and the detectors see it that way: every
  match-relevant element is taken as the engine would receive it, i.e.
  after ``element.rule`` normalization.
- **Detectors are independent.** Each fires on its own condition; a rule
  can collect warnings from several detectors (a bare drop is both a netvm
  denial and a DNS denial when hostname rules follow). Combining or
  suppressing overlapping warnings is the UI's job, not the analyzer's.
- **netvm_addrs are the caller's knowledge.** The core has no qubesadmin
  and resolves nothing: the adapter passes the netvm-facing addresses of
  the qube (gateway / DNS endpoints, IPv4 and/or IPv6 strings). With no
  addresses given, only the bare-drop form of netvm denial can fire —
  documented, tested, and the honest limit of offline analysis. A drop
  rule aimed at a HOSTNAME is likewise undecidable offline (no resolution
  by design); it does not fire detector 1.
- **Detector 1 boundary:** only a ZERO-selector drop (denies everything,
  hence the netvm) or a destination-wide drop whose dsthost network
  contains a netvm address fires. A service-limited drop ('drop proto=tcp',
  'drop proto=icmp') denies a service everywhere but is not a netvm denial
  — warning on the common "block pings" rule would train users to click
  through warnings. The DNS-shaped cases have detector 2.
- **Detector 2 fires on the card's condition only:** a DNS denial with at
  least one LATER rule that matches on a hostname (any action — a hostname
  drop rule needs resolution to match just as much). DNS denials are:
  'drop specialtarget=dns', any drop whose dstports range covers 53, and
  the bare drop-all (which denies DNS among everything). A dsthost-narrowed
  port-53 drop still fires — offline we cannot know it is not THE DNS
  server; over-warning is the safe direction for a lockout analyzer.
- **Detector 3 is accept-only**, per the card. A bare DROP-all also makes
  later rules dead, but it is reported by detector 1 with the stronger
  lockout framing; double-reporting the same rule as "shadowed" would be
  noise. The reset default state (a single 'action=accept') must produce
  ZERO warnings — tested.
- **``now`` is injectable** (epoch seconds or datetime, naive = UTC) so
  every expiry verdict is deterministic under test; ``None`` means
  ``time.time()``. Expiry comparison is ``ts < now``, strictly earlier,
  mirroring rules.Expire.expired.

safety.py never raises on rule CONTENT: analyze() expects rules that passed
validate.py. Unparseable strings propagate ValueError from the rule model —
loud failure, not a silent skip.
'''

import datetime
import ipaddress
import time

from collections import namedtuple

from .rules import RuleList

__all__ = [
    'NETVM_DENIED', 'DNS_DENIED', 'ACCEPT_ALL_SHADOW', 'RULE_EXPIRED',
    'DUPLICATE_RULE', 'SafetyWarning', 'DETECTOR_ORDER',
    'detect_netvm_denied', 'detect_dns_denied', 'detect_accept_all_shadow',
    'detect_expired', 'detect_duplicates', 'analyze', 'format_warnings',
]

# Warning codes — stable strings the UI keys confirmation dialogs off.
NETVM_DENIED = 'netvm-denied'
DNS_DENIED = 'dns-denied'
ACCEPT_ALL_SHADOW = 'accept-all-shadow'
RULE_EXPIRED = 'rule-expired'
DUPLICATE_RULE = 'duplicate-rule'

#: analyze() runs the detectors in the card's order.
DETECTOR_ORDER = (NETVM_DENIED, DNS_DENIED, ACCEPT_ALL_SHADOW,
                  RULE_EXPIRED, DUPLICATE_RULE)

_WarningBase = namedtuple('SafetyWarning', ('code', 'indexes', 'message'))


class SafetyWarning(_WarningBase):
    '''One finding: (code, indexes, message).

    ``indexes`` is a tuple of rule indexes in evaluation order — the
    offending rule first, then any rules it affects (shadowed, dependent,
    duplicated). ``message`` is self-contained human text (it names the
    indexes and the rule), ready for a confirmation dialog.'''

    __slots__ = ()

    def __new__(cls, code, indexes, message):
        return _WarningBase.__new__(cls, code, tuple(indexes), message)


def _coerce(rules):
    '''Accept RuleList / iterable of Rule / iterable of API-form strings.'''
    if isinstance(rules, RuleList):
        return list(rules)
    return list(RuleList(rules))


def _epoch(now):
    '''Normalize the injectable clock to float epoch seconds.

    None -> time.time(); int/float -> itself; datetime -> timestamp
    (naive interpreted as UTC, so tests are timezone-independent; aware
    converted properly). bool is rejected: a programming error must not
    silently analyze at epoch 0/1.'''
    if now is None:
        return time.time()
    if isinstance(now, bool):
        raise TypeError('now must be epoch seconds or a datetime, not bool')
    if isinstance(now, (int, float)):
        return float(now)
    if isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=datetime.timezone.utc).timestamp()
        return now.timestamp()
    raise TypeError('now must be epoch seconds or a datetime, got {}'
                    .format(type(now).__name__))


def _expire_ts(rule):
    '''Absolute expire timestamp of a rule, or None. rules.Rule resolves
    '+seconds' at construction, so the stored value is always absolute.'''
    if rule.expire is None:
        return None
    return int(str(rule.expire))


def _is_expired(rule, now):
    timestamp = _expire_ts(rule)
    # Strictly earlier, mirroring rules.Expire.expired ('<' not '<=').
    return timestamp is not None and timestamp < now


def _effective_dsthost(rule):
    '''The dsthost as the ENGINE will see it: None when absent, and None
    when the client model normalizes it away (0.0.0.0/0, ::/0 — "any host"
    is expressed by absence, so such a rule is a match-all).'''
    dsthost = rule.dsthost
    if dsthost is None or dsthost.rule is None:
        return None
    return dsthost


def _selectors(rule):
    '''Match-relevant elements in the serialized (engine) view. expire and
    comment are NOT selectors — they do not narrow what a rule matches.'''
    return (_effective_dsthost(rule), rule.proto, rule.dstports,
            rule.icmptype, rule.specialtarget)


def _is_match_all(rule):
    '''True when the rule narrows nothing: it matches every packet.'''
    return all(element is None for element in _selectors(rule))


def _is_drop(rule):
    return str(rule.action) == 'drop'


def _denies_dns(rule):
    '''True when this drop rule denies DNS traffic (see module docstring,
    detector 2): specialtarget=dns, a dstports range covering 53, or a bare
    match-all drop (which denies everything, DNS included).'''
    if not _is_drop(rule):
        return False
    dsthost, proto, dstports, icmptype, specialtarget = _selectors(rule)
    if specialtarget is not None and str(specialtarget) == 'dns':
        return True
    if dstports is not None:
        low, high = dstports.range
        if low <= 53 <= high:
            return True
    return (dsthost is None and proto is None and dstports is None
            and icmptype is None and specialtarget is None)


def _needs_resolution(rule):
    '''True when the rule matches on a hostname — an element that only means
    something if DNS resolution works (see module docstring, detector 2).'''
    dsthost = _effective_dsthost(rule)
    return dsthost is not None and dsthost.type == 'dsthost'


def _parse_netvm_addrs(netvm_addrs):
    '''Parse caller-supplied netvm addresses up front. An unparseable
    address is a caller bug and raises ValueError immediately — a silently
    skipped address would silently disable lockout detection.'''
    parsed = []
    for addr in netvm_addrs:
        parsed.append(ipaddress.ip_address(addr))
    return parsed


def detect_netvm_denied(rules, netvm_addrs=(), now=None):
    '''Detector 1 — rules that deny the qube's own netvm.

    Fires on an active (non-expired) drop rule that either

    - carries NO match selectors at all: it denies every packet, including
      everything to and through the netvm — total lockout; or
    - is destination-wide (no proto/dstports/icmptype/specialtarget) and its
      dsthost network contains one of ``netvm_addrs``: it denies all traffic
      to the netvm itself (gateway services, DNS via netvm, updates proxy).

    A drop whose dsthost is a hostname cannot be decided offline (no name
    resolution by design) and does not fire; pass resolved addresses in
    ``netvm_addrs`` instead. With ``netvm_addrs`` empty only the bare form
    can fire — the documented limit of offline analysis.'''
    now = _epoch(now)
    netvm_ips = _parse_netvm_addrs(netvm_addrs)
    warnings = []
    for index, rule in enumerate(_coerce(rules)):
        if _is_expired(rule, now) or not _is_drop(rule):
            continue
        dsthost, proto, dstports, icmptype, specialtarget = _selectors(rule)
        if (dsthost is None and proto is None and dstports is None
                and icmptype is None and specialtarget is None):
            warnings.append(SafetyWarning(
                NETVM_DENIED, (index,),
                'rule {} ({}) is a bare drop: it denies every packet, '
                'including all traffic to and through the qube\'s netvm — '
                'saving this list locks the qube out of its network'
                .format(index, rule.rule)))
        elif (dsthost is not None and dsthost.type in ('dst4', 'dst6')
                and proto is None and dstports is None and icmptype is None
                and specialtarget is None):
            network = ipaddress.ip_network(str(dsthost), strict=False)
            for addr in netvm_ips:
                if addr.version == network.version and addr in network:
                    warnings.append(SafetyWarning(
                        NETVM_DENIED, (index,),
                        'rule {} ({}) drops all traffic to {}, which is an '
                        'address of the qube\'s netvm — the qube loses its '
                        'gateway services'
                        .format(index, rule.rule, addr)))
                    break
    return warnings


def detect_dns_denied(rules, now=None):
    '''Detector 2 — a DNS denial with later resolution-dependent rules.

    Fires for every active drop rule that denies DNS (see _denies_dns) when
    at least one LATER active rule matches on a hostname: that rule's
    hostname only means something while resolution works, so the earlier
    DNS denial makes the later rule (and the qube's name resolution)
    unreliable or dead. Order is the point: a hostname rule BEFORE the DNS
    denial does not fire it, and neither does a DNS denial with no hostname
    rules after it (the card's condition, deliberately).'''
    now = _epoch(now)
    rules = _coerce(rules)
    active = [(index, rule) for index, rule in enumerate(rules)
              if not _is_expired(rule, now)]
    warnings = []
    for index, rule in active:
        if not _denies_dns(rule):
            continue
        dependents = tuple(j for j, later in active
                           if j > index and _needs_resolution(later))
        if dependents:
            warnings.append(SafetyWarning(
                DNS_DENIED, (index,) + dependents,
                'rule {} ({}) denies DNS, but rule(s) {} match on hostnames '
                'that need DNS resolution — they will not reliably work '
                'while DNS is denied'
                .format(index, rule.rule,
                        ', '.join(str(j) for j in dependents))))
    return warnings


def detect_accept_all_shadow(rules, now=None):
    '''Detector 3 — accept-all rules that shadow every rule after them.

    An active rule with action=accept and no selectors matches every packet,
    so in first-match evaluation order nothing after it can ever match —
    later rules are decoration the engine will never consult. Fires for
    EVERY such rule that has at least one rule after it (a lone accept-all
    — the state `reset` writes — shadows nothing and is clean). Expired
    accept-alls do not fire (the engine skips them; detector 4 reports
    them). Later rules count as shadowed regardless of their own expiry —
    they are still shown in the grid and still dead.'''
    now = _epoch(now)
    rules = _coerce(rules)
    warnings = []
    for index, rule in enumerate(rules):
        if _is_expired(rule, now):
            continue
        if str(rule.action) != 'accept' or not _is_match_all(rule):
            continue
        if index + 1 >= len(rules):
            continue  # nothing after it: nothing shadowed (reset state)
        shadowed = tuple(range(index + 1, len(rules)))
        warnings.append(SafetyWarning(
            ACCEPT_ALL_SHADOW, (index,) + shadowed,
            'rule {} ({}) accepts everything; rule(s) {} after it can never '
            'match — the engine stops at the first match'
            .format(index, rule.rule,
                    ', '.join(str(j) for j in shadowed))))
    return warnings


def detect_expired(rules, now=None):
    '''Detector 4 — rules whose expire time has already passed.

    The engine skips expired rules when applying and silently DROPS them at
    the next save (qubes/firewall.py save() / qdb_entries). A user looking
    at the grid may still believe such a rule protects or permits something;
    this warning says it is already dead weight. Strictly-earlier comparison
    ('ts < now'), mirroring rules.Expire.expired, so a rule expiring exactly
    at ``now`` is not yet expired. Messages render the timestamp in UTC —
    the engine's own expiry check uses naive local time, but the verdict is
    an epoch comparison and timezone-independent.'''
    now = _epoch(now)
    warnings = []
    for index, rule in enumerate(_coerce(rules)):
        timestamp = _expire_ts(rule)
        if timestamp is None or timestamp >= now:
            continue
        when = datetime.datetime.fromtimestamp(
            timestamp, datetime.timezone.utc).isoformat()
        warnings.append(SafetyWarning(
            RULE_EXPIRED, (index,),
            'rule {} ({}) expired at {} — the engine already skips it and '
            'it will be dropped at the next save'
            .format(index, rule.rule, when)))
    return warnings


def detect_duplicates(rules):
    '''Detector 5 — exact duplicates.

    "Exact" means identical in the serialized API form (rule.rule), which
    is the equality the engine and the delete-by-match path use: input
    spelling does not matter ('dstports=443' and 'dstports=443-443' are the
    same rule; a differing comment makes rules different). Only the first
    copy can ever match; later ones are dead weight and usually a UI/CLI
    mistake. Structural check: fires regardless of expiry (an expired
    duplicate is still a duplicate; detector 4 adds its own warning).
    One warning per duplicated rule, listing ALL of its indexes.'''
    rules = _coerce(rules)
    groups = {}
    for index, rule in enumerate(rules):
        groups.setdefault(rule.rule, []).append(index)
    warnings = []
    for serialized, indexes in groups.items():
        if len(indexes) < 2:
            continue
        warnings.append(SafetyWarning(
            DUPLICATE_RULE, tuple(indexes),
            'rules {} are exact duplicates ({}); only the first can ever '
            'match'.format(', '.join(str(i) for i in indexes),
                           serialized)))
    return warnings


def analyze(rules, netvm_addrs=(), now=None):
    '''Run every detector over the whole ordered list, before any write.

    Returns all warnings in detector order (DETECTOR_ORDER — the card's
    order); within a detector, indexes ascend. ``now`` is resolved ONCE
    here so all five detectors share one clock (a list analyzed "at" one
    instant cannot have a rule expired for one detector and active for
    another). An empty list analyzes to no warnings; the reset default
    state (a single bare accept) also produces none — asserted in tests.'''
    now = _epoch(now)
    rules = _coerce(rules)
    warnings = []
    warnings.extend(detect_netvm_denied(rules, netvm_addrs, now))
    warnings.extend(detect_dns_denied(rules, now))
    warnings.extend(detect_accept_all_shadow(rules, now))
    warnings.extend(detect_expired(rules, now))
    warnings.extend(detect_duplicates(rules))
    return warnings


def format_warnings(warnings):
    '''Human-readable block for confirmation dialogs and the dry-run print.

    One '[code] message' line per warning, in the given order; the empty
    string when there is nothing to warn about (the UI then skips the
    confirmation step entirely).'''
    return '\n'.join('[{}] {}'.format(warning.code, warning.message)
                     for warning in warnings)
