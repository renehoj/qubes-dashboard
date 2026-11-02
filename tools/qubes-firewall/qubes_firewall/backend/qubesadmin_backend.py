"""Backend talking to qubesd through qubesadmin (dom0 or a GUI qube).

Reads and writes go through ``vm.firewall``, the ``admin.vm.firewall.Get`` /
``Set`` path ``qvm-firewall`` uses. Rules are handed to qubesadmin as
``qubesadmin.firewall.Rule`` objects built from keywords, never as strings, so
qubesadmin's own code serializes what qubesd receives (qvm-firewall-ui's
``adapter.core_rule_kwargs``).
"""

from __future__ import annotations

import qubesadmin
import qubesadmin.exc
import qubesadmin.firewall

from ..model import ADMIN_VM, QubeFirewall
from ..rules import Rule, RuleList
from .base import Backend, BackendError

#: The DNS addresses every networked qube gets (qubes-core-admin's
#: ``NetVMMixin.dns``). The only net qube addresses readable over the Admin
#: API, so the only ones the lockout check can be given.
DNS_ADDRESSES = ("10.139.1.1", "10.139.1.2")


def core_rule_kwargs(rule: Rule) -> dict[str, str]:
    """Keywords that rebuild ``rule`` as a ``qubesadmin.firewall.Rule``."""
    kwargs = {"action": str(rule.action)}
    if rule.proto is not None:
        kwargs["proto"] = str(rule.proto)
    if rule.dsthost is not None:
        # dst4, dst6 or dsthost: the keywords qubesadmin's Rule accepts.
        kwargs[rule.dsthost.type] = str(rule.dsthost)
    for prop in ("specialtarget", "dstports", "icmptype", "expire", "comment"):
        value = getattr(rule, prop)
        if value is not None:
            kwargs[prop] = str(value)
    return kwargs


class QubesAdminBackend(Backend):
    """The real thing."""

    name = "qubesd"

    def __init__(self, app=None):
        self.app = app if app is not None else qubesadmin.Qubes()

    def _vm(self, name: str):
        try:
            return self.app.domains[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def get_firewall(self, name: str) -> QubeFirewall:
        vm = self._vm(name)
        try:
            klass = vm.klass
            if klass == ADMIN_VM:
                return QubeFirewall(name, klass, None, RuleList())
            netvm = vm.netvm
            rules = self._read_rules(name)
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(str(exc)) from exc
        networked = netvm is not None
        if not networked:
            try:
                networked = bool(vm.provides_network)
            except qubesadmin.exc.QubesException:
                networked = False
        return QubeFirewall(name, klass, str(netvm) if netvm else None, rules,
                            DNS_ADDRESSES if networked else ())

    def _read_rules(self, name: str) -> RuleList:
        firewall = self._vm(name).firewall
        try:
            # Firewall.rules is cached per instance; load_rules() forces a Get.
            firewall.load_rules()
            return RuleList(rule.rule for rule in firewall.rules)
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(f"Could not read the firewall of {name}: {exc}") from exc
        except ValueError as exc:
            raise BackendError(f"The firewall of {name} has a rule this window "
                               f"cannot read: {exc}") from exc

    def _write_rules(self, name: str, rules: RuleList) -> None:
        engine_rules = [qubesadmin.firewall.Rule(None, **core_rule_kwargs(rule))
                        for rule in rules]
        try:
            self._vm(name).firewall.save_rules(engine_rules)
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(f"qubesd refused the rules: {exc}") from exc
        except UnicodeEncodeError as exc:
            raise BackendError(f"A rule is not plain ASCII: {exc}") from exc
