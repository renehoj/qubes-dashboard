"""Backend talking to qubesd through qubesadmin (dom0 or a GUI qube).

``get_settings`` and ``apply_settings`` are qubes-manager's
(``backend/qubesadmin_backend.py``), with the choice lists computed from a
single cached domain listing instead of three.

The application menu goes through ``qvm-appmenus`` (qubes-desktop-linux-common,
in dom0), the way Qubes' own Create Qube does: ``--get-available`` for what the
template offers, ``--set-whitelist - --update`` to save and regenerate the menu.
The current selection is the qube's ``menu-items`` feature.
"""

from __future__ import annotations

import os
from typing import Any

import qubesadmin
import qubesadmin.exc

from ..model import DEFAULT, MENU_ITEMS, NA, AppEntry, PropValue, QubeSettings
from .appmenus import APPMENUS_DIR, GET_AVAILABLE_ARGS, parse_available, run_appmenus
from .base import Backend, BackendError

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # pylint: disable=broad-except
        return default


def _get(vm, prop: str, default=None):
    return _safe(lambda: getattr(vm, prop), default)


def _name(value) -> str | None:
    """qubesadmin returns VM/label objects for reference properties."""
    return str(value) if value else None


def _is_default(vm, prop: str) -> bool:
    return _safe(lambda: vm.property_is_default(prop), False)


def _prop_value(vm, prop: str, vm_ref: bool = False) -> PropValue:
    conv = _name if vm_ref else (lambda v: v)
    value = conv(_get(vm, prop))
    default = conv(_safe(lambda: vm.property_get_default(prop)))
    return PropValue(value, _is_default(vm, prop), default)


class _Errors:
    """Context manager translating qubesadmin errors into BackendError."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None or issubclass(exc_type, BackendError):
            return False
        if issubclass(exc_type, KeyError):
            raise BackendError(f"No such qube: {exc}") from exc
        if issubclass(exc_type, qubesadmin.exc.QubesException):
            raise BackendError(str(exc) or exc_type.__name__) from exc
        return False


class QubesAdminBackend(Backend):

    name = "qubesadmin"

    @staticmethod
    def _app(cached: bool = False):
        # A fresh Qubes object per call keeps worker threads independent and
        # avoids stale caches. With caching on, qubesadmin fetches all of a
        # qube's properties in one call (admin.vm.property.GetAll).
        app = qubesadmin.Qubes()
        if cached:
            app.cache_enabled = True
        return app

    def get_settings(self, name: str) -> QubeSettings:
        with _Errors():
            listing = self._app(cached=True)
            domains = list(listing.domains)

            def names(pred) -> list[str]:
                return sorted(vm.name for vm in domains if _safe(lambda: pred(vm), False))

            templates = names(lambda vm: vm.klass == "TemplateVM")
            dispvm_templates = names(lambda vm: vm.template_for_dispvms)
            network = names(lambda vm: vm.provides_network)

            # Uncached for the qube itself, so the values are current.
            vm = self._app().domains[name]
            klass = vm.klass
            apps, menu_items, apps_error = self._apps(vm)
            if klass == "DispVM":
                template_choices = dispvm_templates
            elif klass == "AppVM":
                template_choices = templates
            else:
                template_choices = []
            return QubeSettings(
                name=vm.name,
                klass=klass,
                state=_safe(vm.get_power_state, NA),
                label=_name(_get(vm, "label")) or "black",
                template=_name(_get(vm, "template")) if template_choices else None,
                netvm=_prop_value(vm, "netvm", vm_ref=True),
                default_dispvm=_prop_value(vm, "default_dispvm", vm_ref=True),
                memory=_prop_value(vm, "memory"),
                maxmem=_prop_value(vm, "maxmem"),
                vcpus=_prop_value(vm, "vcpus"),
                virt_mode=_prop_value(vm, "virt_mode"),
                include_in_backups=bool(_get(vm, "include_in_backups")),
                template_for_dispvms=bool(_get(vm, "template_for_dispvms")),
                template_choices=template_choices,
                netvm_choices=[n for n in network if n != name],
                dispvm_choices=dispvm_templates,
                apps=apps,
                menu_items=menu_items,
                apps_error=apps_error,
            )

    @staticmethod
    def _apps(vm) -> tuple[list[AppEntry], list[str], str | None]:
        """Available applications, the menu's current ones, and any error."""
        if vm.klass == "AdminVM":
            return [], [], None
        menu_items = (_safe(lambda: vm.features.get("menu-items", ""), "") or "").split()
        # Icons live with the qube that provided the .desktop files: the qube
        # itself, its template, or (disposables) its template's template.
        chain, current = [], vm
        while current is not None and len(chain) < 3:
            chain.append(str(current))
            current = _get(current, "template")
        icon_dirs = [os.path.join(APPMENUS_DIR, n, "apps.tempicons") for n in chain]
        try:
            output = run_appmenus(*GET_AVAILABLE_ARGS, vm.name)
        except BackendError as exc:
            return [], menu_items, str(exc)
        return parse_available(output, icon_dirs), menu_items, None

    def apply_settings(self, name: str, changes: dict[str, Any]) -> None:
        changes = dict(changes)
        menu_items = changes.pop(MENU_ITEMS, None)
        with _Errors():
            vm = self._app().domains[name]
            for prop, value in changes.items():
                if value is DEFAULT:
                    delattr(vm, prop)
                else:
                    setattr(vm, prop, value)
        if menu_items is not None:
            run_appmenus("--set-whitelist", "-", "--update", name,
                      stdin="".join(f"{ident}\n" for ident in menu_items))
