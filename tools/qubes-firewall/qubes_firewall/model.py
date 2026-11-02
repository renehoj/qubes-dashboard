"""What the firewall window shows and edits, without GTK or qubesadmin.

The rules themselves are qvm-firewall-ui's model (``rules``, ``validate`` and
``safety``, copied unchanged): a :class:`~qubes_firewall.rules.RuleList` in
evaluation order, validated the way qubesd validates it, and analysed for
lockouts before a save. This module adds what one window over one qube needs:

* :class:`QubeFirewall`, the plain data a backend hands the window;
* the cells of a rule's row (:func:`rule_cells`) and the window's subtitle;
* :class:`RuleForm`, the rule editor's fields, and the conversion both ways
  (:func:`form_from_rule`, :func:`rule_from_form`);
* :class:`EditSession`, the working copy the buttons change until Save;
* :func:`check_save`, the problems and warnings shown before anything is
  written.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import time

from . import safety, validate
from .rules import Rule, RuleList

ADMIN_VM = "AdminVM"

ACCEPT = "accept"
DROP = "drop"
ACTIONS = (ACCEPT, DROP)
ACTION_TEXT = {ACCEPT: "Allow", DROP: "Block"}

ANY = ""
PROTOCOLS = (ANY, "tcp", "udp", "icmp")
PROTOCOL_TEXT = {ANY: "any", "tcp": "TCP", "udp": "UDP", "icmp": "ICMP"}
PORT_PROTOCOLS = ("tcp", "udp")

#: Column titles of the rule list, matching :func:`rule_cells`.
COLUMNS = ("#", "Action", "Destination", "Protocol", "Port / type", "Expires", "Comment")

#: The last row of the list: what happens to traffic no rule matched.
IMPLICIT_TEXT = "Everything else is blocked."

#: Shown when the qube has no net qube: the rules are kept, not enforced.
NO_NETVM_NOTE = ("This qube has no net qube. The rules are kept and take "
                 "effect when it gets one.")
ADMIN_NOTE = "dom0 has no firewall."

EXPIRE_FORMAT = "%Y-%m-%d %H:%M"
EXPIRE_HINT = "empty = never, +30m, +2h, +1d or 2026-12-31 18:00"
_RELATIVE = re.compile(r"^\+(\d+)\s*([smhd]?)$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclasses.dataclass
class QubeFirewall:
    """One qube's firewall, as read from the backend."""

    name: str
    klass: str
    netvm: str | None
    rules: RuleList
    #: Addresses of the net qube a rule could block (``safety.analyze``).
    netvm_addresses: tuple = ()

    @property
    def is_admin(self) -> bool:
        return self.klass == ADMIN_VM


def summary(fw: QubeFirewall) -> str:
    """The window's subtitle: net qube and rule count."""
    if fw.is_admin:
        return ADMIN_NOTE
    count = len(fw.rules)
    rules = "1 rule" if count == 1 else f"{count} rules"
    netvm = f"net qube {fw.netvm}" if fw.netvm else "no net qube"
    return f"{netvm} · {rules}"


# -- rows ----------------------------------------------------------------------
def expire_text(rule: Rule, now: float | None = None) -> str:
    """When the rule expires, in local time; "expired" once it has."""
    if rule.expire is None:
        return ""
    when = int(str(rule.expire))
    if when <= (time.time() if now is None else now):
        return "expired"
    return datetime.datetime.fromtimestamp(when).strftime(EXPIRE_FORMAT)


def destination_text(rule: Rule) -> str:
    parts = []
    if rule.dsthost is not None and rule.dsthost.rule is not None:
        parts.append(str(rule.dsthost))
    if rule.specialtarget is not None:
        parts.append("DNS servers")
    return ", ".join(parts) or "any"


def port_text(rule: Rule) -> str:
    if rule.dstports is not None:
        return str(rule.dstports)
    if rule.icmptype is not None:
        return f"type {rule.icmptype}"
    return ""


def rule_cells(index: int, rule: Rule, now: float | None = None) -> tuple[str, ...]:
    """The row for ``rule`` at ``index``, one string per entry of COLUMNS."""
    return (
        str(index),
        ACTION_TEXT[str(rule.action)],
        destination_text(rule),
        PROTOCOL_TEXT[str(rule.proto) if rule.proto is not None else ANY],
        port_text(rule),
        expire_text(rule, now),
        str(rule.comment) if rule.comment is not None else "",
    )


# -- the rule editor's fields --------------------------------------------------
@dataclasses.dataclass
class RuleForm:
    """The editor's fields as text, the way the widgets hold them."""

    action: str = ACCEPT
    dsthost: str = ""
    dns: bool = False
    proto: str = ANY
    dstports: str = ""
    icmptype: str = ""
    expire: str = ""
    comment: str = ""


def ports_enabled(form: RuleForm) -> bool:
    return form.proto in PORT_PROTOCOLS


def icmptype_enabled(form: RuleForm) -> bool:
    return form.proto == "icmp"


def form_from_rule(rule: Rule | None) -> RuleForm:
    """The editor's fields for ``rule``; defaults for a new one."""
    if rule is None:
        return RuleForm()
    when = ""
    if rule.expire is not None:
        when = datetime.datetime.fromtimestamp(int(str(rule.expire))).strftime(EXPIRE_FORMAT)
    dsthost = ""
    if rule.dsthost is not None and rule.dsthost.rule is not None:
        dsthost = str(rule.dsthost)
    return RuleForm(
        action=str(rule.action),
        dsthost=dsthost,
        dns=rule.specialtarget is not None,
        proto=str(rule.proto) if rule.proto is not None else ANY,
        dstports=str(rule.dstports) if rule.dstports is not None else "",
        icmptype=str(rule.icmptype) if rule.icmptype is not None else "",
        expire=when,
        comment=str(rule.comment) if rule.comment is not None else "",
    )


def parse_expire(text: str, now: float | None = None) -> str | None:
    """Absolute epoch seconds for the Expires field, None for never.

    Accepts ``+N`` with an optional unit (s, m, h, d) counted from ``now``, and
    a local date and time as EXPIRE_FORMAT. Raises ValueError otherwise.
    """
    text = text.strip()
    if not text:
        return None
    now = time.time() if now is None else now
    match = _RELATIVE.match(text)
    if match:
        return str(int(now) + int(match.group(1)) * _UNITS[match.group(2)])
    try:
        when = datetime.datetime.strptime(text, EXPIRE_FORMAT)
    except ValueError:
        raise ValueError(f"Expires: {EXPIRE_HINT}.") from None
    return str(int(when.timestamp()))


_FIELD_TITLES = {
    "action": "Action", "dsthost": "Destination", "proto": "Protocol",
    "dstports": "Ports", "icmptype": "ICMP type", "specialtarget": "DNS servers",
    "expire": "Expires", "comment": "Comment",
}


def _problem_text(problem: validate.Problem) -> str:
    """``Field: message``, unless the message names the field already."""
    title = _FIELD_TITLES.get(problem.field, problem.field)
    if problem.message.lower().startswith(title.lower()):
        return problem.message[0].upper() + problem.message[1:]
    return f"{title}: {problem.message}"


def rule_from_form(form: RuleForm, now: float | None = None) -> tuple[Rule | None, list[str]]:
    """The rule the editor holds, or None and what is wrong with it.

    Fields the protocol disables are left out, as are empty ones; what is left
    goes through ``validate.validate_fields``, the engine's own acceptance
    rules, before a Rule is built.
    """
    fields: dict[str, str | None] = {"action": form.action}
    if form.dsthost.strip():
        fields["dsthost"] = form.dsthost.strip()
    if form.dns:
        fields["specialtarget"] = "dns"
    if form.proto:
        fields["proto"] = form.proto
    if ports_enabled(form) and form.dstports.strip():
        fields["dstports"] = form.dstports.strip().replace(" ", "")
    if icmptype_enabled(form) and form.icmptype.strip():
        fields["icmptype"] = form.icmptype.strip()
    if form.comment.strip():
        fields["comment"] = form.comment.strip()
    try:
        expire = parse_expire(form.expire, now)
    except ValueError as exc:
        return None, [str(exc)]
    if expire is not None:
        fields["expire"] = expire

    problems = validate.validate_fields(**fields)
    if problems:
        return None, [_problem_text(p) for p in problems]
    try:
        return Rule(None, **fields), []
    except ValueError as exc:  # validate should have caught it; never crash
        return None, [str(exc)]


# -- the working copy ------------------------------------------------------------
class EditSession:
    """The rule list as edited in the window, against the one read from qubesd.

    Every method takes and returns indexes in evaluation order, so the window
    can keep the selection on the rule the user is working with.
    """

    def __init__(self, original: RuleList):
        self.original = RuleList(original.rules)
        self.rules = RuleList(original.rules)

    @property
    def dirty(self) -> bool:
        return self.rules != self.original

    def add(self, rule: Rule, after: int | None = None) -> int:
        """Insert below ``after`` (the selected rule), or at the end."""
        if after is None or after >= len(self.rules) - 1:
            return self.rules.append(rule)
        return self.rules.insert_before(after + 1, rule)

    def replace(self, index: int, rule: Rule) -> int:
        self.rules.delete_by_index(index)
        return self.rules.insert_before(index, rule)

    def delete(self, index: int) -> int | None:
        """Remove a rule; the index to select next, None when the list is empty."""
        self.rules.delete_by_index(index)
        if not len(self.rules):
            return None
        return min(index, len(self.rules) - 1)

    def move_up(self, index: int) -> int:
        return self.rules.move_up(index)

    def move_down(self, index: int) -> int:
        return self.rules.move_down(index)


def check_save(fw: QubeFirewall, rules: RuleList,
               now: float | None = None) -> tuple[list[str], list[str]]:
    """``(problems, warnings)`` for writing ``rules`` to ``fw``'s qube.

    Problems block the save (a rule qubesd would refuse). Warnings are
    ``safety.analyze``'s: lockouts, DNS denied before hostname rules, rules
    shadowed by an accept-all, expired rules, duplicates. The window asks for
    confirmation before saving with warnings.
    """
    problems = [f"Rule {p.index}: {p.message}" for p in validate.validate_rule_list(rules)]
    if problems:
        return problems, []
    warnings = safety.analyze(rules, netvm_addrs=fw.netvm_addresses, now=now)
    return [], [w.message for w in warnings if not _is_closing_drop(rules, w)]


def _is_closing_drop(rules: RuleList, warning: safety.SafetyWarning) -> bool:
    """A bare drop as the last rule, after something was allowed.

    That is the "allow these, block the rest" list qube Settings writes, and
    it blocks no more than the implicit drop after it would. safety.analyze
    still calls it a lockout (it looks at the drop alone); here that warning
    would only teach people to click past warnings.
    """
    if warning.code != safety.NETVM_DENIED or warning.indexes != (len(rules) - 1,):
        return False
    last = rules[len(rules) - 1]
    # A comment does not narrow what a rule matches; an expiry does.
    bare = last.rule.partition(" comment=")[0] == "action=drop"
    return bare and any(str(r.action) == ACCEPT for r in rules.rules[:-1])
