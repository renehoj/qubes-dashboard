"""What the window shows and what Enter offers, as plain Python.

Nothing here imports ``gi`` or ``qubesadmin``: the filtering, ordering and the
"which actions make sense in this state" rules are the parts worth testing, and
they are tested on a box that has neither.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable

# Power states as reported by qubesd (``vm.get_power_state()``).
RUNNING = "Running"
HALTED = "Halted"
PAUSED = "Paused"
TRANSIENT = "Transient"
HALTING = "Halting"
DYING = "Dying"
CRASHED = "Crashed"
NA = "NA"

#: States that count as "on" for the default view. Transient (starting),
#: Halting and Dying are included on purpose: a qube that is on its way up or
#: down is exactly the one you just acted on, and it should not vanish from the
#: list until it has actually stopped.
ON_STATES = frozenset({RUNNING, PAUSED, TRANSIENT, HALTING, DYING})

START = "start"
STOP = "stop"
RESTART = "restart"
APPLICATIONS = "applications"
SETTINGS = "settings"
FIREWALL = "firewall"
DEVICES = "devices"
INFORMATION = "information"
LOGS = "logs"
BACKUP = "backup"
CONSOLE = "console"

#: Dialog order. Also the order Up/Down walks the buttons in.
ACTIONS = (START, STOP, RESTART, APPLICATIONS, SETTINGS, FIREWALL, DEVICES, INFORMATION,
           LOGS, BACKUP, CONSOLE)

#: Shown in the template column for qubes that have no template, by class.
NO_TEMPLATE_TEXT = {
    "TemplateVM": "template",
    "StandaloneVM": "standalone",
    "AdminVM": "admin",
}


@dataclasses.dataclass(frozen=True)
class Qube:
    """One row of the list."""

    name: str
    klass: str
    label: str
    state: str
    template: str | None = None
    #: Icon name in the Qubes icon theme, e.g. ``appvm-red``. Empty when the
    #: backend does not know it; the frontend then draws the label colour.
    icon: str = ""

    @property
    def is_on(self) -> bool:
        return self.state in ON_STATES

    @property
    def is_admin(self) -> bool:
        return self.klass == "AdminVM"


@dataclasses.dataclass(frozen=True)
class App:
    """One of a qube's applications, as its menu lists it."""

    #: The desktop file's name in the qube, without ``.desktop``; what
    #: ``qubes.StartApp+<ident>`` starts.
    ident: str
    name: str
    #: An icon file, or a name in the icon theme; empty when unknown.
    icon: str = ""


def template_text(qube: Qube) -> tuple[str, bool]:
    """Text for the template column, and whether it is a placeholder.

    A placeholder (the qube's class, because it has no template) is drawn dim
    so that it does not read as a template called "standalone".
    """
    if qube.template:
        return qube.template, False
    return NO_TEMPLATE_TEXT.get(qube.klass, ""), True


def tokens_of(query: str) -> list[str]:
    return query.casefold().split()


def matches(qube: Qube, tokens: Iterable[str]) -> bool:
    """Every token must be a substring of the qube name (case-insensitive).

    Only the name is searched: it is the one column that identifies a qube, and
    matching on template too would make ``debian`` return every qube built on
    it, which is a different question from "where is my qube".
    """
    name = qube.name.casefold()
    return all(token in name for token in tokens)


def visible_qubes(
    qubes: Iterable[Qube],
    query: str = "",
    show_all: bool = False,
    keep: Iterable[str] = (),
) -> list[Qube]:
    """The rows to show, in display order.

    ``keep`` names qubes that stay visible regardless of state -- the ones with
    an action in flight, so that a qube started from the "show all" view does
    not disappear the moment that view is switched off again.

    Order: names that start with the first search word come first, then the
    rest, each group alphabetically. With an empty query that is simply
    alphabetical.
    """
    tokens = tokens_of(query)
    keep = set(keep)
    shown = [
        qube
        for qube in qubes
        if (show_all or qube.is_on or qube.name in keep) and matches(qube, tokens)
    ]
    first = tokens[0] if tokens else ""

    def key(qube: Qube):
        name = qube.name.casefold()
        return (not name.startswith(first), name)

    return sorted(shown, key=key)


def available_actions(qube: Qube, busy: bool = False) -> set[str]:
    """Actions that make sense for ``qube`` in its current state.

    ``busy`` means an action from this window is still running for that qube;
    only Settings, Firewall, Devices, Information, Logs and Backup are offered
    then, because a second start/stop would race the first.

    Firewall rules can be changed in any state: a halted qube keeps them, and
    a running one gets them at once. dom0 has no firewall. Devices is there in
    every state too, but its window only changes PCI devices while the qube is
    halted, and only shows them otherwise; dom0 has none of its own.
    Information and Logs only read, so they are there for every qube in every
    state. Backup is there in every state too: a running qube is backed up as
    of its last shutdown, and its window only reverts a qube that is halted;
    dom0's backup is its home directory, which that window does not handle.
    Console attaches to a running qube's console, so it needs a running
    qube with nothing in flight; dom0 has no such console. Applications is
    there for a running qube and for one that is off, which starting an
    application starts as the Qubes menu does; not for a paused qube, where
    the application would wait for a resume, nor while something is in
    flight. dom0's applications are in the Qubes menu itself.
    """
    if qube.is_admin:
        # dom0 cannot be started, stopped or restarted from here, but
        # qubes-vm-settings does have a dom0 page.
        return {SETTINGS, INFORMATION, LOGS}
    windows = {SETTINGS, FIREWALL, DEVICES, INFORMATION, LOGS, BACKUP}
    if busy:
        return windows
    if qube.state in (HALTED, CRASHED, NA):
        return {START, APPLICATIONS} | windows
    if qube.state == PAUSED:
        return {START, STOP} | windows
    if qube.state == RUNNING:
        return {STOP, RESTART, APPLICATIONS, CONSOLE} | windows
    # Transient, Halting, Dying: on the way somewhere already.
    return windows


def default_action(qube: Qube, busy: bool = False) -> str:
    """The button that has focus when the dialog opens.

    Start when that is possible, otherwise Settings -- never Stop or Restart.
    Enter opens the dialog and Enter again activates the focused button, so a
    double Enter on a running qube must not shut it down.
    """
    actions = available_actions(qube, busy)
    return START if START in actions else SETTINGS


def action_label(action: str, qube: Qube) -> str:
    """Button text. A paused qube is resumed, not started."""
    if action == START and qube.state == PAUSED:
        return "Resume"
    return action.capitalize()


#: Asked about once more before they run: they end what runs in the qube.
CONFIRMED_ACTIONS = frozenset({STOP, RESTART})

#: What the confirmation says under its question.
CONFIRM_DETAIL = {
    STOP: "Shuts the qube down. Unsaved work in it is lost.",
    RESTART: "Shuts the qube down and starts it again. Unsaved work in it is lost.",
}


def confirm_question(action: str, qube: Qube) -> str:
    """The confirmation's title, e.g. ``Stop work?``."""
    return f"{action_label(action, qube)} {qube.name}?"


#: Single-key shortcuts in the dialog, and the letter underlined in the label.
#: Not plain first letters: Start, Stop and Settings all begin with S.
ACTION_KEYS = {START: "s", STOP: "t", RESTART: "r", APPLICATIONS: "a", SETTINGS: "e",
               FIREWALL: "f", DEVICES: "d", INFORMATION: "i", LOGS: "l", BACKUP: "b",
               CONSOLE: "c"}

#: What the status column says while an action from this window is running.
PENDING_TEXT = {START: "Starting…", STOP: "Stopping…", RESTART: "Restarting…"}


def matching_apps(apps: Iterable[App], query: str) -> list[App]:
    """``apps`` whose name contains every word of ``query``, in their order."""
    tokens = tokens_of(query)
    return [app for app in apps if all(t in app.name.casefold() for t in tokens)]
