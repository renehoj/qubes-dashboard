"""What the settings window shows and saves, as plain Python.

Nothing here imports ``gi`` or ``qubesadmin``: which fields a qube has, which of
them can be changed in its current state, what the combo boxes offer and which
properties differ from what was loaded are the parts worth testing, and they
are tested on a box that has neither.

The data types and rules are qubes-manager's (``backend/base.py`` and the
``SettingsDialog`` in ``dialogs.py``), with the Qt widgets taken out.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# Power states as reported by qubesd (``vm.get_power_state()``).
RUNNING = "Running"
HALTED = "Halted"
PAUSED = "Paused"
TRANSIENT = "Transient"
HALTING = "Halting"
DYING = "Dying"
CRASHED = "Crashed"
NA = "NA"

LABELS = ["red", "orange", "yellow", "green", "gray", "blue", "purple", "black"]

VIRT_MODES = ["pvh", "hvm", "pv"]

#: Largest value the memory spin buttons accept, in MiB.
MEMORY_MAX = 1024 * 1024
VCPUS_MAX = 256


class _Default:
    """Sentinel meaning "reset this property to its default value"."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "DEFAULT"


DEFAULT = _Default()

#: Combo box ids for "default (x)" and "(none)". GTK ids are C strings, so a
#: NUL byte (qubes-manager's choice) cannot be used; qube names cannot start
#: with a colon, so these never collide with one.
DEFAULT_ID = ":default"
NONE_ID = ":none"


@dataclasses.dataclass(frozen=True)
class PropValue:
    """A property value together with its default-ness."""

    value: Any
    is_default: bool = False
    default: Any = None


@dataclasses.dataclass(frozen=True)
class AppEntry:
    """An application the qube's menu can list (one ``.desktop`` file)."""

    #: The .desktop file name, as qvm-appmenus and ``menu-items`` use it.
    ident: str
    name: str
    comment: str = ""
    #: Path to the template's uncoloured icon, or "" when there is none.
    icon: str = ""


@dataclasses.dataclass
class QubeSettings:
    """Editable settings of one qube, as shown in the settings window."""

    name: str
    klass: str
    state: str
    label: str
    template: str | None
    netvm: PropValue
    default_dispvm: PropValue
    memory: PropValue
    maxmem: PropValue
    vcpus: PropValue
    virt_mode: PropValue
    include_in_backups: bool
    template_for_dispvms: bool
    # Choices for the combo boxes.
    template_choices: list[str] = dataclasses.field(default_factory=list)
    netvm_choices: list[str] = dataclasses.field(default_factory=list)
    dispvm_choices: list[str] = dataclasses.field(default_factory=list)
    #: Applications available for the menu, and the ones listed in it now.
    apps: list[AppEntry] = dataclasses.field(default_factory=list)
    menu_items: list[str] = dataclasses.field(default_factory=list)
    #: Why the application list could not be read, if it could not.
    apps_error: str | None = None

    @property
    def is_halted(self) -> bool:
        return self.state == HALTED

    @property
    def is_admin(self) -> bool:
        return self.klass == "AdminVM"


# -- which fields, and when they can be changed --------------------------------
LABEL = "label"
TEMPLATE = "template"
NETVM = "netvm"
DEFAULT_DISPVM = "default_dispvm"
INCLUDE_IN_BACKUPS = "include_in_backups"
TEMPLATE_FOR_DISPVMS = "template_for_dispvms"
MEMORY = "memory"
MAXMEM = "maxmem"
VCPUS = "vcpus"
VIRT_MODE = "virt_mode"
MENU_ITEMS = "menu_items"

BASIC_FIELDS = (LABEL, TEMPLATE, NETVM, DEFAULT_DISPVM,
                INCLUDE_IN_BACKUPS, TEMPLATE_FOR_DISPVMS)
APPLICATION_FIELDS = (MENU_ITEMS,)
ADVANCED_FIELDS = (MEMORY, MAXMEM, VCPUS, VIRT_MODE)

FIELD_TITLES = {
    LABEL: "Label",
    TEMPLATE: "Template",
    NETVM: "Networking",
    DEFAULT_DISPVM: "Default disposable",
    INCLUDE_IN_BACKUPS: "Include in backups",
    TEMPLATE_FOR_DISPVMS: "Disposable template",
    MEMORY: "Initial memory",
    MAXMEM: "Max memory",
    VCPUS: "VCPUs",
    VIRT_MODE: "Virtualization",
    MENU_ITEMS: "Applications",
}

#: Why a field is greyed out; shown as its tooltip.
HALTED_ONLY = "Can only be changed while the qube is shut down."
DOM0_FIXED = "dom0's label cannot be changed."
NOT_DISPOSABLE_BASE = "Only app qubes and standalone qubes can be disposable templates."
NO_APP_LIST = "The list of applications could not be read."


def shown_fields(s: QubeSettings) -> list[str]:
    """The fields the window has rows for, in order.

    dom0 has no template, networking, disposables or memory settings of its own,
    so it only gets its label (read-only) and the backup flag. A qube without a
    template (templates, standalones) has no Template row.
    """
    if s.is_admin:
        return [LABEL, INCLUDE_IN_BACKUPS]
    fields = [f for f in BASIC_FIELDS if f != TEMPLATE or s.template_choices]
    return fields + list(APPLICATION_FIELDS) + list(ADVANCED_FIELDS)


def disabled_reason(s: QubeSettings, field: str) -> str | None:
    """Why ``field`` cannot be changed now, or None if it can."""
    if field == LABEL and s.is_admin:
        return DOM0_FIXED
    if field == TEMPLATE_FOR_DISPVMS and s.klass not in ("AppVM", "StandaloneVM"):
        return NOT_DISPOSABLE_BASE
    if (field == TEMPLATE or field in ADVANCED_FIELDS) and not s.is_halted:
        return HALTED_ONLY
    if field == MENU_ITEMS and s.apps_error:
        return NO_APP_LIST
    return None


# -- combo box contents --------------------------------------------------------
def prop_id(prop: PropValue) -> str:
    """The combo id that stands for ``prop``'s current value."""
    if prop.is_default:
        return DEFAULT_ID
    return NONE_ID if prop.value is None else str(prop.value)


def id_value(combo_id: str) -> Any:
    """The property value a combo id stands for (DEFAULT, None or a name)."""
    if combo_id == DEFAULT_ID:
        return DEFAULT
    if combo_id == NONE_ID:
        return None
    return combo_id


def vm_options(choices: list[str], *, default: Any = False, allow_none: bool = False,
               current: str | None = None) -> list[tuple[str, str]]:
    """``(id, text)`` pairs: "default (x)", "(none)" and the qube names.

    ``default=False`` means the property has no default option. ``current`` is
    appended when it is not among the choices, so the window always shows the
    value the qube actually has (qubes-manager's ``_select`` does the same).
    """
    options = []
    if default is not False:
        options.append((DEFAULT_ID, f"default ({default or 'none'})"))
    if allow_none:
        options.append((NONE_ID, "(none)"))
    options.extend((name, name) for name in choices)
    if current is not None and current not in (i for i, _t in options):
        options.append((current, current))
    return options


def template_options(s: QubeSettings) -> list[tuple[str, str]]:
    return vm_options(s.template_choices, current=s.template)


def netvm_options(s: QubeSettings) -> list[tuple[str, str]]:
    return vm_options(s.netvm_choices, default=s.netvm.default, allow_none=True,
                      current=prop_id(s.netvm))


def dispvm_options(s: QubeSettings) -> list[tuple[str, str]]:
    return vm_options(s.dispvm_choices, default=s.default_dispvm.default,
                      allow_none=True, current=prop_id(s.default_dispvm))


def virt_mode_options(s: QubeSettings) -> list[tuple[str, str]]:
    default = s.virt_mode.default or "n/a"
    return [(DEFAULT_ID, f"default ({default})")] + [(m, m) for m in VIRT_MODES]


# -- the application menu -------------------------------------------------------
def app_entries(s: QubeSettings) -> list[AppEntry]:
    """Every entry the picker lists: the available ones, by name, then any
    entry the menu has that is no longer available (shown by file name, so it
    can still be seen and removed rather than silently dropped)."""
    entries = sorted(s.apps, key=lambda a: (a.name.casefold(), a.ident))
    known = {a.ident for a in s.apps}
    entries += [AppEntry(i, i, "Not available in this qube's template any more.")
                for i in s.menu_items if i not in known]
    return entries


def ordered_menu_items(s: QubeSettings, selected) -> list[str]:
    """``selected`` idents in picker order, which is also the order saved."""
    chosen = set(selected)
    return [a.ident for a in app_entries(s) if a.ident in chosen]


def matches(entry: AppEntry, query: str) -> bool:
    """Search in the picker: name or file name contains the text, any case.

    One word only: Space selects in the picker, so it never reaches the text.
    """
    query = query.strip().casefold()
    return not query or query in entry.name.casefold() or query in entry.ident.casefold()


# -- what the form holds, and what changed ------------------------------------
def initial_values(s: QubeSettings) -> dict[str, Any]:
    """The form's starting values, in the shape :func:`changes` reads.

    Combo fields hold combo ids, spin fields ints, check fields bools, and
    the application menu a list of .desktop file names.
    """
    return {
        LABEL: s.label,
        TEMPLATE: s.template,
        NETVM: prop_id(s.netvm),
        DEFAULT_DISPVM: prop_id(s.default_dispvm),
        INCLUDE_IN_BACKUPS: s.include_in_backups,
        TEMPLATE_FOR_DISPVMS: s.template_for_dispvms,
        MEMORY: int(s.memory.value or 0),
        MAXMEM: int(s.maxmem.value or 0),
        VCPUS: int(s.vcpus.value or 0),
        VIRT_MODE: prop_id(s.virt_mode),
        MENU_ITEMS: list(s.menu_items),
    }


def changes(s: QubeSettings, values: dict[str, Any]) -> dict[str, Any]:
    """Properties in ``values`` that differ from ``s``, ready for apply_settings.

    Fields that are not shown, or not changeable in the qube's current state,
    are never reported, whatever ``values`` holds for them.
    """
    before = initial_values(s)
    out: dict[str, Any] = {}
    for field in shown_fields(s):
        if field not in values or disabled_reason(s, field):
            continue
        value = values[field]
        if field == MENU_ITEMS:
            if set(value) != set(before[field]):
                out[field] = ordered_menu_items(s, value)
            continue
        if value == before[field]:
            continue
        if field in (NETVM, DEFAULT_DISPVM, VIRT_MODE):
            value = id_value(value)
        out[field] = value
    return out


def summary(s: QubeSettings) -> str:
    """The line under the qube's name: class, state and template."""
    return " · ".join(part for part in (s.klass, s.state, s.template) if part)
