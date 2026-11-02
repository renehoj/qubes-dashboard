"""Backend talking to qubesd through qubesadmin (dom0 or a GUI qube).

Reads what ``qvm-prefs``, ``qvm-features``, ``qvm-service``, ``qvm-tags``,
``qvm-volume`` and ``qvm-firewall`` print, and ``admin.vm.CurrentState`` for a
running qube. Properties come in one ``admin.vm.property.GetAll`` (qubesadmin's
cache, as in qubes-manager); features, tags, volumes and the firewall cost a
call or a few each. Every part is read on its own, so one refused call (an
Admin API policy, an old qubesd) empties that section and says why instead of
failing the window.
"""

from __future__ import annotations

import qubesadmin
import qubesadmin.exc

from ..model import (
    FEATURES, FIREWALL, PROPERTIES, STORAGE, TAGS, LiveStats, Prop, QubeInfo, Volume,
)
from .base import Backend, BackendError


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # pylint: disable=broad-except
        return default


def _text(value) -> str:
    """A property value as ``qvm-prefs`` prints it (VMs and labels by name)."""
    if value is None:
        return ""
    return str(value)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


class QubesAdminBackend(Backend):
    """The real thing."""

    name = "qubesd"

    @staticmethod
    def _app(cached: bool = False):
        # A fresh Qubes object per call keeps worker threads independent and
        # avoids stale caches; see qubes-dashboard.
        app = qubesadmin.Qubes()
        if cached:
            app.cache_enabled = True
        return app

    @staticmethod
    def _vm(app, name: str):
        try:
            return app.domains[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(_message(exc)) from exc

    def get_info(self, name: str) -> QubeInfo:
        app = self._app(cached=True)
        vm = self._vm(app, name)
        try:
            klass = vm.klass
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(_message(exc)) from exc
        state = _safe(vm.get_power_state, "NA")
        errors: list[tuple[str, str]] = []

        def part(section: str, reader, default):
            try:
                return reader()
            except Exception as exc:  # pylint: disable=broad-except
                errors.append((section, _message(exc)))
                return default

        props = part(PROPERTIES, lambda: self._props(vm), ())
        features = part(FEATURES, lambda: self._features(vm), ())
        tags = part(TAGS, lambda: tuple(sorted(str(t) for t in vm.tags)), ())
        admin = klass == "AdminVM"
        volumes = () if admin else part(STORAGE, lambda: self._volumes(vm), ())
        firewall = None if admin else part(
            FIREWALL, lambda: tuple(rule.rule for rule in vm.firewall.rules), None)
        stats = self._stats(app, vm) if state == "Running" else None
        return QubeInfo(name=name, klass=klass, state=state, props=props,
                        features=features, tags=tags, volumes=volumes,
                        firewall=firewall, stats=stats, errors=tuple(errors))

    @staticmethod
    def _props(vm) -> tuple[Prop, ...]:
        result = []
        for prop in sorted(vm.property_list()):
            try:
                value = _text(getattr(vm, prop))
            except AttributeError:
                # No value and no default (qvm-prefs prints "-").
                value = None
            result.append(Prop(prop, value, bool(_safe(lambda p=prop: vm.property_is_default(p)))))
        return tuple(result)

    @staticmethod
    def _features(vm) -> tuple[tuple[str, str], ...]:
        features = vm.features
        return tuple((key, _text(features.get(key, ""))) for key in sorted(features.keys()))

    @staticmethod
    def _volumes(vm) -> tuple[Volume, ...]:
        volumes = vm.volumes
        result = []
        for vol_name in sorted(volumes.keys()):
            vol = volumes[vol_name]

            def get(attr, default=None, vol=vol):
                return _safe(lambda: getattr(vol, attr), default)

            result.append(Volume(
                name=vol_name,
                pool=_text(get("pool", "")),
                vid=_text(get("vid", "")),
                size=_int(get("size")),
                usage=_int(get("usage")),
                rw=bool(get("rw", True)),
                snap_on_start=bool(get("snap_on_start", False)),
                save_on_stop=bool(get("save_on_stop", False)),
                source=_text(get("source")) or None,
                ephemeral=get("ephemeral"),
                revisions_to_keep=_int(get("revisions_to_keep")),
                revisions=tuple(str(r) for r in (get("revisions") or ())),
                outdated=bool(_safe(vol.is_outdated, False)),
            ))
        return tuple(result)

    @staticmethod
    def _stats(app, vm) -> LiveStats | None:
        """``admin.vm.CurrentState``: "mem=… mem_static_max=… cputime=… power_state=…"."""
        try:
            out = app.qubesd_call(vm.name, "admin.vm.CurrentState").decode()
        except Exception:  # pylint: disable=broad-except
            return None
        fields = dict(f.split("=", 1) for f in out.split() if "=" in f)
        cputime = _int(fields.get("cputime"))
        start = _safe(lambda: float(vm.start_time or 0)) or None
        return LiveStats(
            memory_kb=_int(fields.get("mem")),
            max_memory_kb=_int(fields.get("mem_static_max")),
            # libvirt's cputime is in nanoseconds.
            cpu_seconds=cputime / 1e9 if cputime is not None else None,
            start_time=start,
        )

    def get_stats(self, name: str) -> LiveStats | None:
        app = self._app()
        vm = self._vm(app, name)
        if _safe(vm.get_power_state) != "Running":
            return None
        return self._stats(app, vm)
