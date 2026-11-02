"""GTK frontend for qubes-dashboard.

One window: a search field and a grid of qubes (Name, Status, Template). By
default only qubes that are on are listed; Ctrl+H, or the checkbox next to the
search field, shows every qube, and so does typing a search. Enter on a row opens a small dialog with Start,
Stop, Restart, Applications, Settings, Firewall, Devices, Information, Logs,
Backup and Console -- nothing is ever done to a qube by Enter alone. Settings opens qubes-settings' window over
the list, in this process when the ``qubes_settings`` package can be imported
(see ``_settings_window_class``); Firewall does the same with qubes-firewall's
(``_firewall_window_class``), Devices with qubes-devices'
(``_devices_window_class``), Information with qubes-information's
(``_information_window_class``), Logs with qubes-logs'
(``_logs_window_class``), and Backup with qubes-backup's
(``_backup_window_class``).

The window is qubes-launcher's: same undecorated chrome, the stock Qubes menu's
stylesheet with the launcher's red selection on top, the same keyboard contract
(type anywhere to search, Up/Down move, Escape clears then hides, Ctrl+Q quits)
and the same resident, D-Bus-activated single instance. What it does is
qubes-manager's: its backend split, its demo data, and its way of keeping state
current -- one cheap ``admin.vm.List`` per second while the window is visible,
and a full reload when the set of qubes changes.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-launcher's frontend, which mirrors ``qubes_menu.appmenu`` (the Qubes
OS Project, LGPL-2.1+). Off dom0, where ``qubes_menu`` is not installed,
``fallback-theme.css`` stands in for the stock colour definitions.
"""

from __future__ import annotations

import dataclasses
import html
import importlib.resources
import logging
import sys
import threading

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-launcher and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import KINDS, Backend, BackendError, create_backend  # noqa: E402

logger = logging.getLogger("qubes-dashboard")


def _settings_window_class():
    """qubes-settings' SettingsWindow, or None when it is not importable.

    Imported on first use, not at startup: the list must work without it, and
    then runs a settings command instead (``backend.settings_command``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_settings.window import SettingsWindow
    except ImportError:
        return None
    return SettingsWindow


def _firewall_window_class():
    """qubes-firewall's FirewallWindow, or None when it is not importable.

    Imported on first use, like qubes-settings; without it, Firewall runs the
    ``qubes-firewall`` command instead (``backend.FIREWALL_COMMAND``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_firewall.window import FirewallWindow
    except ImportError:
        return None
    return FirewallWindow


def _devices_window_class():
    """qubes-devices' DevicesWindow, or None when it is not importable.

    Imported on first use, like qubes-settings; without it, Devices runs the
    ``qubes-devices`` command instead (``backend.DEVICES_COMMAND``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_devices.window import DevicesWindow
    except ImportError:
        return None
    return DevicesWindow


def _information_window_class():
    """qubes-information's InformationWindow, or None when it is not importable.

    Imported on first use, like qubes-settings; without it, Information runs
    the ``qubes-information`` command instead (``backend.INFORMATION_COMMAND``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_information.window import InformationWindow
    except ImportError:
        return None
    return InformationWindow


def _logs_window_class():
    """qubes-logs' LogsWindow, or None when it is not importable.

    Imported on first use, like qubes-settings; without it, Logs runs the
    ``qubes-logs`` command instead (``backend.LOGS_COMMAND``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_logs.window import LogsWindow
    except ImportError:
        return None
    return LogsWindow


def _backup_window_class():
    """qubes-backup's BackupWindow, or None when it is not importable.

    Imported on first use, like qubes-settings; without it, Backup runs the
    ``qubes-backup`` command instead (``backend.BACKUP_COMMAND``).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from qubes_backup.window import BackupWindow
    except ImportError:
        return None
    return BackupWindow

#: Referenced by packaging/: the resident instance answers on this bus name.
APP_ID = "org.qubesos.dashboard"
WINDOW_NAME = "qubes-dashboard-window"
DIALOG_NAME = "qubes-dashboard-dialog"
PANE_CLASS = "dashboard-pane"
#: On the main window while the action dialog is open over it.
COVERED_CLASS = "covered"
#: Window background opacity; see qubes-launcher's README, "Window styling".
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
#: How often power states are refreshed while the window is visible.
POLL_MS = 1000
#: When the list asks for the focus again after a dialog over it closed.
REFOCUS_MS = 100
#: How long --qube waits for the list to get the focus before opening the
#: action dialog anyway.
ACTIVE_WAIT_MS = 500
#: How long a closing dialog stays while the list takes the focus back.
CLOSE_WAIT_MS = 300
#: How long an error stays in the footer.
ERROR_MS = 8000
ICON_SIZE = 16
#: The applications dialog scrolls beyond this height.
APPS_MAX_HEIGHT = 360

#: Qubes label colours (qubes-core-admin's default labels), used to draw the
#: label square when the Qubes icon theme is not available.
LABEL_COLORS = {
    "red": 0xCC0000,
    "orange": 0xF57900,
    "yellow": 0xEDD400,
    "green": 0x73D216,
    "gray": 0x8A8A8A,
    "blue": 0x3465A4,
    "purple": 0x75507B,
    "black": 0x555555,
}

#: Status column colours. Anything not listed is drawn dim.
STATUS_COLORS = {
    model.RUNNING: "#A3BE8C",
    model.PAUSED: "#EBCB8B",
    model.CRASHED: "#BF616A",
}
#: In-between states, and pending actions, are drawn italic in this colour.
MOVING_COLOR = "#EBCB8B"

(
    COL_NAME,
    COL_ICON,
    COL_NAME_MARKUP,
    COL_STATUS_MARKUP,
    COL_STATUS_SORT,
    COL_TEMPLATE_MARKUP,
    COL_TEMPLATE_SORT,
) = range(7)


# -- theme (same as qubes-launcher) ----------------------------------------
def _is_theme_light(widget: Gtk.Widget) -> bool:
    """Is the current system theme light? (mirrors qubes_menu.appmenu)"""
    context = widget.get_style_context()
    background = context.get_background_color(Gtk.StateFlags.NORMAL)
    text = context.get_color(Gtk.StateFlags.NORMAL)
    background_intensity = background.red + background.blue + background.green
    text_intensity = text.red + text.blue + text.green
    return text_intensity < background_intensity


def _add_css(css: str) -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _load_theme(window: Gtk.Widget) -> bool:
    """Apply the stock menu's light or dark stylesheet; False if not installed.

    The light/dark files ``@import`` qubes-menu-base.css and define the colour
    names dashboard.css is written against.
    """
    try:
        package = importlib.resources.files("qubes_menu")
    except ImportError:
        return False
    name = "qubes-menu-light.css" if _is_theme_light(window) else "qubes-menu-dark.css"
    resource = package / name
    if not resource.is_file():
        return False
    with importlib.resources.as_file(resource) as path:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(path))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return True


def _own_css(name: str) -> str:
    return (importlib.resources.files("qubes_dashboard") / name).read_text(
        encoding="utf-8"
    )


def _prepare_transparency(window: Gtk.Window) -> bool:
    """Give the window an RGBA visual so CSS alpha is honoured.

    Must run before the window is first realised, or it stays opaque for good.
    """
    screen = window.get_screen()
    visual = screen.get_rgba_visual() if screen.is_composited() else None
    if visual is None:
        return False
    window.set_visual(visual)
    window.set_app_paintable(True)
    return True


def _set_chrome(window: Gtk.Window, name: str) -> None:
    """The stock menu's window chrome: undecorated, no taskbar/pager entry."""
    window.set_name(name)
    window.set_keep_above(True)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_decorated(False)
    window.set_deletable(False)


# -- small helpers -----------------------------------------------------------
def _close_over_parent(dialog: Gtk.Window) -> None:
    """Destroy ``dialog`` once its parent, the list, has the focus back.

    Destroying the focused dialog leaves the next focus to the window manager,
    and xfwm4 may give it to the window from before the list -- nearly always
    when the list was opened with --qube -- and then refuse the list's
    present(). Asked while the dialog still has the focus, the activation goes
    from one of our windows to another, which it allows. Gives up waiting
    after CLOSE_WAIT_MS.
    """
    parent = dialog.get_transient_for()
    if parent is None or not parent.get_visible() or parent.is_active():
        dialog.destroy()
        return
    # A modal dialog would take its parent's activation for itself.
    dialog.set_modal(False)
    handler = timeout = 0

    def done():
        nonlocal handler, timeout
        if not handler:
            return
        parent.disconnect(handler)
        handler = 0
        if timeout:
            GLib.source_remove(timeout)
            timeout = 0
        dialog.destroy()

    def on_active(window, _pspec):
        if window.is_active():
            done()

    def on_timeout():
        nonlocal timeout
        timeout = 0
        done()
        return False

    handler = parent.connect("notify::is-active", on_active)
    timeout = GLib.timeout_add(CLOSE_WAIT_MS, on_timeout)
    parent.present_with_time(Gtk.get_current_event_time())


def run_in_thread(fn, *args, on_done=None, on_error=None) -> None:
    """Run a blocking backend call off the GTK thread; report back on it."""

    def deliver(callback, value):
        if callback is not None:
            callback(value)
        return False

    def work():
        try:
            result = fn(*args)
        except Exception as exc:  # pylint: disable=broad-except
            GLib.idle_add(deliver, on_error, exc)
            return
        GLib.idle_add(deliver, on_done, result)

    threading.Thread(target=work, daemon=True).start()


def highlight_markup(name: str, tokens: list[str]) -> str:
    """``name`` as Pango markup, with every search-word occurrence in bold."""
    lower = name.lower()
    marked = [False] * len(name)
    for token in tokens:
        start = lower.find(token)
        while token and start != -1:
            for i in range(start, start + len(token)):
                marked[i] = True
            start = lower.find(token, start + 1)
    out = []
    i = 0
    while i < len(name):
        j = i
        while j < len(name) and marked[j] == marked[i]:
            j += 1
        chunk = html.escape(name[i:j])
        out.append(f"<b>{chunk}</b>" if marked[i] else chunk)
        i = j
    return "".join(out)


def status_markup(qube: model.Qube, pending: str | None) -> str:
    if pending:
        return f'<span foreground="{MOVING_COLOR}"><i>{model.PENDING_TEXT[pending]}</i></span>'
    state = html.escape(qube.state)
    if qube.state in STATUS_COLORS:
        return f'<span foreground="{STATUS_COLORS[qube.state]}">{state}</span>'
    if qube.is_on:
        return f'<span foreground="{MOVING_COLOR}"><i>{state}</i></span>'
    return f'<span alpha="60%">{state}</span>'


def template_markup(qube: model.Qube) -> str:
    text, placeholder = model.template_text(qube)
    text = html.escape(text)
    return f'<span alpha="50%">{text}</span>' if placeholder else text


def _underlined(text: str, key: str) -> str:
    """``text`` as markup with the first ``key`` in it underlined."""
    index = text.lower().find(key)
    if index == -1:
        return html.escape(text)
    return (
        html.escape(text[:index])
        + f"<u>{html.escape(text[index])}</u>"
        + html.escape(text[index + 1:])
    )


class _IconCache:
    """Label icons: the Qubes icon theme's when present, else a colour square."""

    def __init__(self):
        self._theme = Gtk.IconTheme.get_default()
        self._cache: dict[tuple[str, str], GdkPixbuf.Pixbuf] = {}

    def get(self, qube: model.Qube) -> GdkPixbuf.Pixbuf:
        key = (qube.icon, qube.label)
        if key not in self._cache:
            self._cache[key] = self._load(qube)
        return self._cache[key]

    def _load(self, qube: model.Qube) -> GdkPixbuf.Pixbuf:
        if qube.icon and self._theme.has_icon(qube.icon):
            try:
                return self._theme.load_icon(
                    qube.icon, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE
                )
            except GLib.Error:
                pass
        size = ICON_SIZE - 4
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
        pixbuf.fill((LABEL_COLORS.get(qube.label, 0x8A8A8A) << 8) | 0xFF)
        return pixbuf


# -- the action dialog -------------------------------------------------------
class ActionDialog(Gtk.Window):
    """Start / Stop / Restart / Settings for one qube.

    Keyboard: Up/Down (or Tab) move between buttons, Enter activates, the
    underlined letter activates directly, Escape goes back to the list.
    Buttons that make no sense in the qube's state are shown but disabled, so
    the dialog always has the same shape.
    """

    def __init__(self, parent: Gtk.Window, qube: model.Qube, busy: bool,
                 on_choose, on_cancel):
        super().__init__(title=qube.name)
        _set_chrome(self, DIALOG_NAME)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(240, -1)
        _prepare_transparency(self)

        self.qube = qube
        self._on_choose = on_choose
        self._on_cancel = on_cancel
        self._closed = False
        self.available = model.available_actions(qube, busy)

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(qube.name)}</b>")
        title.get_style_context().add_class("dialog-title")
        pane.pack_start(title, False, False, 0)

        template, _placeholder = model.template_text(qube)
        subtitle = Gtk.Label(
            label=" · ".join(part for part in (qube.state, template) if part),
            xalign=0,
        )
        subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(subtitle, False, False, 0)

        self.buttons: dict[str, Gtk.Button] = {}
        for action in model.ACTIONS:
            button = Gtk.Button()
            label = Gtk.Label(xalign=0)
            label.set_markup(self._label_markup(action))
            button.add(label)
            button.get_style_context().add_class("action")
            button.set_sensitive(action in self.available)
            button.connect("clicked", self._on_clicked, action)
            pane.pack_start(button, False, False, 0)
            self.buttons[action] = button
        pane.get_children()[-1].get_style_context().add_class("dialog-bottom")

        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        # set_focus before showing: otherwise GTK gives the initial focus to the
        # first sensitive button (Stop, for a running qube) on map, overriding
        # a grab_focus made earlier -- and Enter, Enter would then shut it down.
        default = self.buttons[model.default_action(qube, busy)]
        self.set_focus(default)
        self.show_all()
        default.grab_focus()

    def _label_markup(self, action: str) -> str:
        """The button text with its shortcut letter underlined."""
        return _underlined(model.action_label(action, self.qube), model.ACTION_KEYS[action])

    def _enabled(self) -> list[str]:
        return [a for a in model.ACTIONS if a in self.available]

    def _move(self, step: int) -> None:
        enabled = self._enabled()
        current = next(
            (a for a in enabled if self.buttons[a].has_focus()), enabled[0]
        )
        index = max(0, min(len(enabled) - 1, enabled.index(current) + step))
        self.buttons[enabled[index]].grab_focus()

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move(1 if event.keyval == Gdk.KEY_Down else -1)
            return True
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0).lower()
        for action, key in model.ACTION_KEYS.items():
            if char == key and action in self.available:
                self.choose(action)
                return True
        return False

    def _on_clicked(self, _button, action: str) -> None:
        self.choose(action)

    def _on_focus_out(self, *_args) -> bool:
        # Clicking anywhere else dismisses, like the main window does.
        self.cancel(focus_lost=True)
        return False

    def _finish(self, to_list: bool = False):
        """Close; ``to_list`` when the list is where the user goes next."""
        if self._closed:
            return False
        self._closed = True
        if to_list:
            _close_over_parent(self)
        else:
            self.destroy()
        return True

    def choose(self, action: str) -> None:
        # Start stays in the list; Stop and Restart ask first, in a dialog of
        # their own, and the tools take the list's place.
        if self._finish(to_list=action == model.START):
            self._on_choose(self.qube, action)

    def cancel(self, focus_lost: bool = False) -> None:
        if self._finish(to_list=not focus_lost):
            self._on_cancel(focus_lost)

    def dismiss(self) -> None:
        """Close without reporting back (the list is going away itself)."""
        self._finish()


class ConfirmDialog(Gtk.Window):
    """Asks before Stop or Restart, in the action dialog's place and look.

    The focus starts on Cancel, so Enter, Enter, Enter cannot stop a qube.
    Up/Down (or Tab) move, Enter activates, the action's underlined letter
    confirms, **c** and Escape cancel; either way it reports back once.
    """

    def __init__(self, parent: Gtk.Window, qube: model.Qube, action: str,
                 on_confirm, on_cancel):
        super().__init__(title=qube.name)
        _set_chrome(self, DIALOG_NAME)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(240, -1)
        _prepare_transparency(self)

        self.qube = qube
        self.action = action
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._closed = False

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(model.confirm_question(action, qube))}</b>")
        title.get_style_context().add_class("dialog-title")
        pane.pack_start(title, False, False, 0)

        detail = Gtk.Label(label=model.CONFIRM_DETAIL[action], xalign=0)
        detail.set_line_wrap(True)
        detail.set_max_width_chars(36)
        detail.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(detail, False, False, 0)

        self.confirm_button = self._button(
            _underlined(model.action_label(action, qube), model.ACTION_KEYS[action]))
        self.confirm_button.connect("clicked", lambda *_: self.confirm())
        pane.pack_start(self.confirm_button, False, False, 0)
        self.cancel_button = self._button(_underlined("Cancel", "c"))
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        self.cancel_button.get_style_context().add_class("dialog-bottom")
        pane.pack_start(self.cancel_button, False, False, 0)

        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        # As in ActionDialog: set_focus before showing, or GTK focuses the
        # first button (the action) on map.
        self.set_focus(self.cancel_button)
        self.show_all()
        self.cancel_button.grab_focus()

    @staticmethod
    def _button(markup: str) -> Gtk.Button:
        button = Gtk.Button()
        label = Gtk.Label(xalign=0)
        label.set_markup(markup)
        button.add(label)
        button.get_style_context().add_class("action")
        return button

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            target = self.cancel_button if event.keyval == Gdk.KEY_Down else self.confirm_button
            target.grab_focus()
            return True
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0).lower()
        if char == model.ACTION_KEYS[self.action]:
            self.confirm()
            return True
        if char == "c":
            self.cancel()
            return True
        return False

    def _on_focus_out(self, *_args) -> bool:
        # Clicking anywhere else is a no, like dismissing the action dialog.
        self.cancel(focus_lost=True)
        return False

    def _finish(self, to_list: bool = False) -> bool:
        """Close; ``to_list`` when the list is where the user goes next."""
        if self._closed:
            return False
        self._closed = True
        if to_list:
            _close_over_parent(self)
        else:
            self.destroy()
        return True

    def confirm(self) -> None:
        # Stop and Restart run from the list, which stays.
        if self._finish(to_list=True):
            self._on_confirm(self.qube, self.action)

    def cancel(self, focus_lost: bool = False) -> None:
        if self._finish(to_list=not focus_lost):
            self._on_cancel(focus_lost)

    def dismiss(self) -> None:
        """Close without reporting back (the list is going away itself)."""
        self._finish()


def _app_image(icon: str) -> Gtk.Image:
    """An application's icon: a file, or a name in the icon theme.

    Blank, but as wide, when there is none, so the names stay in line.
    """
    image = Gtk.Image()
    image.set_size_request(ICON_SIZE, ICON_SIZE)
    try:
        if icon.startswith("/"):
            image.set_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_size(icon, ICON_SIZE, ICON_SIZE))
        elif icon and Gtk.IconTheme.get_default().has_icon(icon):
            image.set_from_pixbuf(Gtk.IconTheme.get_default().load_icon(
                icon, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE))
    except GLib.Error:
        pass
    return image


class ApplicationsDialog(Gtk.Window):
    """A qube's applications, in the action dialog's place and look.

    It opens at once and is filled when the backend has read the qube's
    menu (``set_apps``). Typing filters by name, Backspace takes a letter
    off, Up/Down (or Tab) move, Enter starts the focused application, Escape
    clears the filter and then goes back to the list; either way it reports
    back once.
    """

    def __init__(self, parent: Gtk.Window, qube: model.Qube, on_launch, on_cancel):
        super().__init__(title=qube.name)
        _set_chrome(self, DIALOG_NAME)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(240, -1)
        _prepare_transparency(self)

        self.qube = qube
        self._on_launch = on_launch
        self._on_cancel = on_cancel
        self._closed = False
        #: None until loaded.
        self.apps: list[model.App] | None = None
        self.query = ""
        self._error = ""
        self.buttons: list[tuple[model.App, Gtk.Button]] = []

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(qube.name)}</b>")
        title.get_style_context().add_class("dialog-title")
        pane.pack_start(title, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_line_wrap(True)
        self.subtitle.set_max_width_chars(36)
        self.subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(self.subtitle, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(APPS_MAX_HEIGHT)
        scrolled.get_style_context().add_class("dialog-bottom")
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Keeps the focused application scrolled into view.
        self.list_box.set_focus_vadjustment(scrolled.get_vadjustment())
        scrolled.add(self.list_box)
        pane.pack_start(scrolled, False, False, 0)

        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        self._update_subtitle()
        self.show_all()

    def set_apps(self, apps: list[model.App]) -> None:
        """Fill the dialog; the first application gets the focus."""
        if self._closed:
            return
        self.apps = apps
        for app in apps:
            button = Gtk.Button()
            row = Gtk.Box(spacing=8)
            row.pack_start(_app_image(app.icon), False, False, 0)
            row.pack_start(Gtk.Label(label=app.name, xalign=0), True, True, 0)
            button.add(row)
            button.get_style_context().add_class("action")
            button.connect("clicked", lambda _button, app=app: self.launch(app))
            self.list_box.pack_start(button, False, False, 0)
            self.buttons.append((app, button))
        self.list_box.show_all()
        self._apply_filter()

    def set_error(self, text: str) -> None:
        if self._closed:
            return
        self.apps = []
        self._error = text
        self._update_subtitle()

    def visible_buttons(self) -> list[Gtk.Button]:
        return [button for _app, button in self.buttons if button.get_visible()]

    def _apply_filter(self) -> None:
        matching = model.matching_apps(self.apps or [], self.query)
        for app, button in self.buttons:
            button.set_visible(app in matching)
        visible = self.visible_buttons()
        if visible and not any(b.has_focus() for b in visible):
            self.set_focus(visible[0])
            visible[0].grab_focus()
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        if self._error:
            text = self._error
        elif self.apps is None:
            text = "Loading applications…"
        elif not self.apps:
            text = "No applications. Choose them in the qube's Settings, Applications."
        else:
            text = "Applications" if self.qube.is_on else "Applications (starts the qube)"
            if self.query:
                text += f" · {self.query}"
                if not self.visible_buttons():
                    text += " · no match"
        self.subtitle.set_text(text)

    def _move(self, step: int) -> None:
        visible = self.visible_buttons()
        if not visible:
            return
        current = next((b for b in visible if b.has_focus()), visible[0])
        index = max(0, min(len(visible) - 1, visible.index(current) + step))
        visible[index].grab_focus()

    def _set_query(self, query: str) -> None:
        self.query = query
        self._apply_filter()

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.query:
                self._set_query("")
            else:
                self.cancel()
            return True
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move(1 if event.keyval == Gdk.KEY_Down else -1)
            return True
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        if event.keyval == Gdk.KEY_BackSpace:
            self._set_query(self.query[:-1])
            return True
        # Space goes into a filter already begun; on its own it presses the
        # focused button, as in the action dialog.
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0)
        if char.isprintable() and char and (self.query or not char.isspace()):
            self._set_query(self.query + char)
            return True
        return False

    def _on_focus_out(self, *_args) -> bool:
        # Clicking anywhere else dismisses, like the action dialog.
        self.cancel(focus_lost=True)
        return False

    def _finish(self, to_list: bool = False) -> bool:
        """Close; ``to_list`` when the list is where the user goes next."""
        if self._closed:
            return False
        self._closed = True
        if to_list:
            _close_over_parent(self)
        else:
            self.destroy()
        return True

    def launch(self, app: model.App) -> None:
        # The application takes the list's place, as a tool's window does.
        if self._finish():
            self._on_launch(self.qube, app)

    def cancel(self, focus_lost: bool = False) -> None:
        if self._finish(to_list=not focus_lost):
            self._on_cancel(focus_lost)

    def dismiss(self) -> None:
        """Close without reporting back (the list is going away itself)."""
        self._finish()


# -- the application ---------------------------------------------------------
class DashboardApp(Gtk.Application):
    """The list window and its keyboard contract."""

    #: Search text carried by the invocation being handled; see qubes-launcher.
    pending_query: str = ""
    #: Qube named by the invocation being handled (``--qube``), whose action
    #: dialog opens once the window is shown.
    pending_qube: str = ""

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.primary = False
        self.keep_visible = False
        self.start_in_background = False
        self.default_show_all = False
        #: The checkbox as the user left it; a search ticks it only for as long
        #: as there is search text.
        self.user_show_all = False
        self.window_alpha = WINDOW_ALPHA
        self.translucent = False
        self.backend_kind = "auto"
        self.backend: Backend | None = None

        self.window: Gtk.Window | None = None
        self.search_entry: Gtk.SearchEntry | None = None
        self.show_all_check: Gtk.CheckButton | None = None
        self.tree: Gtk.TreeView | None = None
        self.store: Gtk.ListStore | None = None
        self.stack: Gtk.Stack | None = None
        self.placeholder: Gtk.Label | None = None
        self.footer: Gtk.Label | None = None
        #: The action dialog, or the confirmation or applications that took
        #: its place.
        self.dialog: ActionDialog | ConfirmDialog | ApplicationsDialog | None = None
        #: qubes-settings' window, while it is open over the list.
        self.settings_window: Gtk.Window | None = None
        self._settings_backend = None
        #: qubes-firewall's window, while it is open over the list.
        self.firewall_window: Gtk.Window | None = None
        self._firewall_backend = None
        #: qubes-devices' window, while it is open over the list.
        self.devices_window: Gtk.Window | None = None
        self._devices_backend = None
        #: qubes-information's window, while it is open over the list.
        self.information_window: Gtk.Window | None = None
        self._information_backend = None
        #: qubes-logs' window, while it is open over the list.
        self.logs_window: Gtk.Window | None = None
        self._logs_backend = None
        #: qubes-backup's window, while it is open over the list.
        self.backup_window: Gtk.Window | None = None
        self._backup_backend = None
        self.icons: _IconCache | None = None

        #: Every qube, by name, as last read from the backend.
        self.qubes: dict[str, model.Qube] = {}
        #: Qube name -> action running for it from this window.
        self.pending: dict[str, str] = {}
        self._signature = None
        self._polling = False
        self._loading = False
        self._error_timeout = 0
        #: Qube whose action dialog opens when the next full load is in.
        self._dialog_on_load = ""

        self._add_cli_options()

    # -- command line ----------------------------------------------------
    def _add_cli_options(self) -> None:
        def flag(name, short, text):
            self.add_main_option(name, ord(short) if short else 0,
                                 GLib.OptionFlags.NONE, GLib.OptionArg.NONE, text, None)

        flag("keep-visible", "k", "Do not hide the window on focus loss or after an action")
        flag("background", "b", "Do not show the window at start; useful for autostart")
        flag("quit", "q", "Stop the resident instance instead of showing it")
        flag("all", "A", "Show halted qubes too, not only the ones that are on")
        flag("mock", None, "Use built-in demo data (same as --backend mock)")
        self.add_main_option(
            "alpha", ord("a"), GLib.OptionFlags.NONE, GLib.OptionArg.DOUBLE,
            f"Window background opacity, 0.0-1.0 (default {WINDOW_ALPHA})", None,
        )
        self.add_main_option(
            "search", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Open with this text already in the search field", None,
        )
        self.add_main_option(
            "qube", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Open with this qube selected and its action dialog open", "NAME",
        )
        self.add_main_option(
            "backend", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            f"Where qubes come from: {', '.join(KINDS)} (default auto: qubesadmin "
            "when installed, demo data otherwise)", None,
        )

    def do_command_line(self, command_line):  # pylint: disable=arguments-differ
        Gtk.Application.do_command_line(self, command_line)
        options = command_line.get_options_dict().end().unpack()

        if "quit" in options:
            logger.info("Quit requested")
            self.quit()
            return 0
        if "keep-visible" in options:
            self.keep_visible = True
        if "background" in options:
            self.start_in_background = True
        if "all" in options:
            self.default_show_all = True
        if "alpha" in options:
            self.window_alpha = max(0.0, min(1.0, float(options["alpha"])))
        if "backend" in options:
            kind = str(options["backend"])
            if kind not in KINDS:
                command_line.printerr(f"Unknown backend {kind!r}; use one of {', '.join(KINDS)}\n")
                return 2
            self.backend_kind = kind
        if "mock" in options:
            self.backend_kind = "mock"
        if "search" in options:
            self.pending_query = str(options["search"])
        if "qube" in options:
            self.pending_qube = str(options["qube"])
        self.activate()
        return 0

    def do_before_emit(self, platform_data):  # pylint: disable=arguments-differ
        """Take a search term or a qube out of a D-Bus summon's platform data.

        Same route as qubes-launcher, so a hotkey can open a pre-filtered list,
        or one qube's action dialog, without a cold start::

            gdbus call --session --dest org.qubesos.dashboard \\
              --object-path /org/qubesos/dashboard \\
              --method org.freedesktop.Application.Activate "{'search': <'sys'>}"

        ``"{'qube': <'work'>}"`` opens the dialog for ``work``.
        """
        try:
            Gtk.Application.do_before_emit(self, platform_data)
            query = self._string_from_platform_data(platform_data, "search")
            qube = self._string_from_platform_data(platform_data, "qube")
        except Exception:  # pylint: disable=broad-except
            logger.warning("Ignoring platform data", exc_info=True)
            return
        if query:
            self.pending_query = query
        if qube:
            self.pending_qube = qube

    @staticmethod
    def _string_from_platform_data(platform_data, key: str) -> str:
        """The ``key`` string in a caller's platform data, or "".

        Platform data comes from an untrusted process, so non-strings are
        ignored rather than coerced.
        """
        if platform_data is None:
            return ""
        try:
            unpacked = platform_data.unpack()
        except Exception:  # pylint: disable=broad-except
            return ""
        if not isinstance(unpacked, dict):
            return ""
        value = unpacked.get(key)
        return value if isinstance(value, str) else ""

    # -- setup -----------------------------------------------------------
    def perform_setup(self) -> None:
        self.backend = create_backend(self.backend_kind)
        logger.info("Backend: %s", self.backend.name)
        self.icons = _IconCache()
        self._build_ui()
        self._apply_style()
        self._reload()
        GLib.timeout_add(POLL_MS, self._poll)

    def _build_ui(self) -> None:
        title = "Qubes Dashboard"
        if self.backend.is_demo:
            title += f" ({self.backend.name})"
        self.window = Gtk.Window(title=title)
        _set_chrome(self.window, WINDOW_NAME)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_default_size(600, 440)
        self.translucent = _prepare_transparency(self.window)

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.window.add(pane)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        pane.pack_start(top, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.get_style_context().add_class("search_entry")
        self.search_entry.set_placeholder_text("search")
        top.pack_start(self.search_entry, True, True, 0)

        self.show_all_check = Gtk.CheckButton(label="All qubes")
        self.show_all_check.set_tooltip_text("Show halted qubes too (Ctrl+H)")
        self.show_all_check.set_can_focus(False)
        self.show_all_check.get_style_context().add_class("show-all")
        top.pack_start(self.show_all_check, False, False, 0)

        self.store = Gtk.ListStore(str, GdkPixbuf.Pixbuf, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_enable_search(False)
        self.tree.set_headers_visible(True)
        # Focus stays in the search field; the list is driven from there, so
        # typing always searches and the selection is always the keyboard's.
        self.tree.set_can_focus(False)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self._add_columns()

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.tree)

        self.placeholder = Gtk.Label(xalign=0, yalign=0)
        self.placeholder.get_style_context().add_class("placeholder")

        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.placeholder, "empty")
        pane.pack_start(self.stack, True, True, 0)

        self.footer = Gtk.Label(xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        pane.pack_start(self.footer, False, False, 0)

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.show_all_check.connect("toggled", self._on_show_all_toggled)
        self.tree.connect("row-activated", self._on_row_activated)
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("focus-out-event", self._on_focus_out)
        self.window.connect("delete-event", self._on_delete)

        self.add_window(self.window)

    def _add_columns(self) -> None:
        name_column = Gtk.TreeViewColumn(title="Name")
        icon_cell = Gtk.CellRendererPixbuf()
        icon_cell.set_padding(6, 3)
        name_column.pack_start(icon_cell, False)
        name_column.add_attribute(icon_cell, "pixbuf", COL_ICON)
        name_cell = Gtk.CellRendererText()
        name_cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        name_column.pack_start(name_cell, True)
        name_column.add_attribute(name_cell, "markup", COL_NAME_MARKUP)
        name_column.set_expand(True)
        name_column.set_sort_column_id(COL_NAME)

        columns = [name_column]
        for title, markup_col, sort_col, width in (
            ("Status", COL_STATUS_MARKUP, COL_STATUS_SORT, 110),
            ("Template", COL_TEMPLATE_MARKUP, COL_TEMPLATE_SORT, 210),
        ):
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title=title)
            column.pack_start(cell, True)
            column.add_attribute(cell, "markup", markup_col)
            column.set_sort_column_id(sort_col)
            column.set_min_width(width)
            column.set_resizable(True)
            columns.append(column)

        for column in columns:
            self.tree.append_column(column)
            column.get_button().set_can_focus(False)

    def _apply_style(self) -> None:
        if not _load_theme(self.window):
            logger.info("qubes_menu not installed; using fallback-theme.css")
            _add_css(_own_css("fallback-theme.css"))
        alpha = self.window_alpha if self.translucent else 1.0
        _add_css(_own_css("dashboard.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))

    # -- data ------------------------------------------------------------
    def _reload(self) -> None:
        """Re-read every qube (labels, templates, the set of names)."""
        if self._loading:
            return
        self._loading = True
        run_in_thread(self.backend.list_qubes,
                      on_done=self._qubes_loaded, on_error=self._load_failed)

    def _qubes_loaded(self, qubes: list[model.Qube]) -> None:
        self._loading = False
        self.qubes = {qube.name: qube for qube in qubes}
        self._rebuild()
        name, self._dialog_on_load = self._dialog_on_load, ""
        if name and self.window.get_visible():
            self._open_dialog_when_active(name)

    def _load_failed(self, exc: Exception) -> None:
        self._loading = False
        self._polling = False
        self._dialog_on_load = ""
        logger.warning("Could not read qubes: %s", exc)
        self._show_error(f"Could not read qubes: {exc}")

    def _poll(self) -> bool:
        """Timer: refresh power states while the window is visible."""
        if self.window.get_visible() and not self._polling and not self._loading:
            self._polling = True
            run_in_thread(self.backend.power_states,
                          on_done=self._states_loaded, on_error=self._load_failed)
        return True

    def _states_loaded(self, states: dict[str, str]) -> None:
        self._polling = False
        if set(states) != set(self.qubes):
            # A qube was created, removed, or a disposable came or went.
            self._reload()
            return
        for name, state in states.items():
            if self.qubes[name].state != state:
                self.qubes[name] = dataclasses.replace(self.qubes[name], state=state)
        self._rebuild()

    # -- the grid --------------------------------------------------------
    def _rebuild(self, reset_selection: bool = False) -> None:
        """Fill the grid from ``self.qubes``; skipped when nothing changed."""
        query = self.search_entry.get_text()
        show_all = self.show_all_check.get_active()
        rows = model.visible_qubes(self.qubes.values(), query, show_all, keep=self.pending)
        signature = (query, tuple((q, self.pending.get(q.name)) for q in rows))
        if signature == self._signature and not reset_selection:
            return
        self._signature = signature

        selected = None if reset_selection else self._selected_name()
        tokens = model.tokens_of(query)
        self.store.clear()
        for qube in rows:
            template, _placeholder = model.template_text(qube)
            self.store.append([
                qube.name,
                self.icons.get(qube),
                highlight_markup(qube.name, tokens),
                status_markup(qube, self.pending.get(qube.name)),
                # Sort keys: on before off, then by state name.
                f"{0 if qube.is_on else 1}{qube.state}",
                template_markup(qube),
                template.casefold(),
            ])
        self._select(selected)

        if rows:
            self.stack.set_visible_child_name("list")
        else:
            self.placeholder.set_text(
                "No matching qubes" if query
                else "No qubes are running" if not show_all
                else "No qubes"
            )
            self.stack.set_visible_child_name("empty")
        if not self._error_timeout:
            self._set_footer(self._hint(len(rows), show_all))

    def _hint(self, count: int, show_all: bool) -> str:
        what = "qubes" if show_all else "running"
        parts = [f"{count} {what}", "↵ actions", "Ctrl+H all qubes", "Esc close"]
        if self.backend.is_demo:
            parts.append(self.backend.name)
        return "  ·  ".join(parts)

    def _selected_name(self) -> str | None:
        store, tree_iter = self.tree.get_selection().get_selected()
        return store[tree_iter][COL_NAME] if tree_iter is not None else None

    def _selected_qube(self) -> model.Qube | None:
        name = self._selected_name()
        return self.qubes.get(name) if name else None

    def _select(self, name: str | None) -> None:
        """Select ``name`` if it is listed, otherwise the first row."""
        target = None
        for row in self.store:
            if row[COL_NAME] == name:
                target = row.iter
                break
        if target is None:
            target = self.store.get_iter_first()
        selection = self.tree.get_selection()
        if target is None:
            selection.unselect_all()
            return
        selection.select_iter(target)
        self.tree.scroll_to_cell(self.store.get_path(target), None, False, 0, 0)

    def _move_selection(self, step: int) -> None:
        count = len(self.store)
        if not count:
            return
        _store, tree_iter = self.tree.get_selection().get_selected()
        index = self.store.get_path(tree_iter).get_indices()[0] if tree_iter else -1
        index = max(0, min(count - 1, index + step))
        path = Gtk.TreePath.new_from_indices([index])
        self.tree.get_selection().select_path(path)
        self.tree.scroll_to_cell(path, None, False, 0, 0)

    # -- footer ----------------------------------------------------------
    def _set_footer(self, text: str, error: bool = False) -> None:
        self.footer.set_text(text)
        context = self.footer.get_style_context()
        (context.add_class if error else context.remove_class)("error")

    def _show_error(self, text: str) -> None:
        self._set_footer(text, error=True)
        if self._error_timeout:
            GLib.source_remove(self._error_timeout)
        self._error_timeout = GLib.timeout_add(ERROR_MS, self._clear_error)

    def _clear_error(self) -> bool:
        self._error_timeout = 0
        self._signature = None
        self._rebuild()
        return False

    # -- actions ---------------------------------------------------------
    def _open_dialog(self) -> None:
        qube = self._selected_qube()
        if qube is None or self.dialog is not None:
            return
        self._set_dimmed(True)
        self.dialog = ActionDialog(
            self.window, qube, qube.name in self.pending,
            on_choose=self._on_dialog_choose, on_cancel=self._on_dialog_cancel,
        )

    def _open_dialog_when_active(self, name: str) -> None:
        """Open ``name``'s action dialog once the list has the focus.

        Opened before that, the dialog takes the focus from whatever window had
        it, and the window manager gives it back there when the dialog closes,
        refusing the list's own present(). With the list focused first, closing
        the dialog returns to the list, as it does after Enter. A window
        manager that never focuses the list gets the dialog after
        ACTIVE_WAIT_MS.
        """
        if self.window.is_active():
            self._open_dialog_for(name)
            return
        handler = timeout = 0

        def finish():
            nonlocal handler, timeout
            if not handler:
                return
            self.window.disconnect(handler)
            handler = 0
            if timeout:
                GLib.source_remove(timeout)
                timeout = 0
            if self.window.get_visible():
                self._open_dialog_for(name)

        def on_active(window, _pspec):
            if window.is_active():
                finish()

        def on_timeout():
            nonlocal timeout
            timeout = 0
            finish()
            return False

        handler = self.window.connect("notify::is-active", on_active)
        timeout = GLib.timeout_add(ACTIVE_WAIT_MS, on_timeout)

    def _open_dialog_for(self, name: str) -> None:
        """Select ``name`` and open its action dialog (``--qube``).

        A search that hides the qube is cleared, and a halted qube ticks
        *All qubes*, so the qube is in the list when the dialog closes.
        """
        qube = self.qubes.get(name)
        if qube is None:
            self._show_error(f"No qube named {name}")
            return
        if self.search_entry.get_text() and not model.matches(
                qube, model.tokens_of(self.search_entry.get_text())):
            self.search_entry.set_text("")
        if not (qube.is_on or self.show_all_check.get_active()):
            self.show_all_check.set_active(True)
        self._rebuild()
        self._select(name)
        if self._selected_name() == name:
            self._open_dialog()

    def _set_dimmed(self, dimmed: bool) -> None:
        """Fade the list while the action dialog is open over it.

        GTK 3's CSS has no blur filter, so the list is made fainter instead;
        see the ``covered`` rule in dashboard.css.
        """
        context = self.window.get_style_context()
        (context.add_class if dimmed else context.remove_class)(COVERED_CLASS)

    def _on_dialog_cancel(self, focus_lost: bool = False) -> None:
        self.dialog = None
        self._set_dimmed(False)
        if focus_lost:
            # Focus went somewhere else entirely; the list dismisses itself as
            # it would have without the dialog.
            GLib.timeout_add(150, self._hide_if_inactive)
        else:
            # Escape or Cancel: the user is back with the list. A list opened
            # with --qube never had the focus, so the window manager may hand
            # it to the window that had it before (see _refocus_list).
            GLib.timeout_add(REFOCUS_MS, self._refocus_list)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _covered(self) -> bool:
        """Is a window of ours open over the list (and holding the focus)?"""
        return (self.dialog is not None or self.settings_window is not None
                or self.firewall_window is not None
                or self.devices_window is not None
                or self.information_window is not None
                or self.logs_window is not None
                or self.backup_window is not None)

    def _hide_if_inactive(self) -> bool:
        if not self._covered() and not self.window.is_active() and not self.keep_visible:
            self.hide_window()
        return False

    def _on_dialog_choose(self, qube: model.Qube, action: str) -> None:
        if action == model.APPLICATIONS:
            # The list stays faded behind the applications, too.
            dialog = self.dialog = ApplicationsDialog(
                self.window, qube,
                on_launch=self._on_app_launch, on_cancel=self._on_dialog_cancel,
            )
            run_in_thread(
                self.backend.list_apps, qube.name,
                on_done=dialog.set_apps, on_error=lambda exc: dialog.set_error(str(exc)),
            )
            return
        if action in model.CONFIRMED_ACTIONS:
            # The list stays faded behind the question.
            self.dialog = ConfirmDialog(
                self.window, qube, action,
                on_confirm=self._on_dialog_confirm, on_cancel=self._on_dialog_cancel,
            )
            return
        self._on_dialog_confirm(qube, action)

    def _on_dialog_confirm(self, qube: model.Qube, action: str) -> None:
        self.dialog = None
        self._set_dimmed(False)
        if action in (model.START, model.STOP, model.RESTART) or self.keep_visible:
            # A tool window otherwise takes the list's place (_open_tool).
            self.window.present()
            self.search_entry.grab_focus_without_selecting()
            # The dialog was chosen from, so the user is still with the list.
            GLib.timeout_add(REFOCUS_MS, self._refocus_list)
        if action == model.SETTINGS:
            self._open_settings(qube)
            return
        if action == model.FIREWALL:
            self._open_firewall(qube)
            return
        if action == model.DEVICES:
            self._open_devices(qube)
            return
        if action == model.INFORMATION:
            self._open_information(qube)
            return
        if action == model.LOGS:
            self._open_logs(qube)
            return
        if action == model.BACKUP:
            self._open_backup(qube)
            return
        if action == model.CONSOLE:
            self._open_console(qube)
            return

        call = {
            model.START: self.backend.start,
            model.STOP: self.backend.shutdown,
            model.RESTART: self.backend.restart,
        }[action]
        self.pending[qube.name] = action
        self._rebuild()
        run_in_thread(
            call, qube.name,
            on_done=lambda _result: self._action_finished(qube.name),
            on_error=lambda exc: self._action_failed(qube.name, action, exc),
        )

    def _refocus_list(self) -> bool:
        """Take the focus back if the closed dialog's window manager kept it.

        present() right after the dialog is destroyed reaches the window
        manager while the modal dialog is still mapped, so it may focus that,
        and then fall back to some other window once the dialog is gone.
        """
        if self.window.get_visible() and not self.window.is_active() and not self._covered():
            self.window.present()
            self.search_entry.grab_focus_without_selecting()
        return False

    def _open_settings(self, qube: model.Qube) -> None:
        window_class = _settings_window_class()
        if window_class is None:
            try:
                self.backend.open_settings(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            # The settings window takes over; get out of its way.
            self.hide_window()
            return

        backend = self._get_settings_backend()
        if backend.is_demo:
            # Two simulated systems; keep "only while shut down" in step.
            backend.set_states({name: q.state for name, q in self.qubes.items()})
        self.settings_window = self._open_tool(
            window_class, backend, qube, self._on_settings_closed)

    def _open_tool(self, window_class, backend, qube: model.Qube, on_close):
        """Open a tool's window for ``qube``; the list closes and leaves it alone.

        With --keep-visible the list stays, and the window opens over it and
        reports back through ``on_close``; it is returned for tracking then.
        Otherwise the window is on its own, as the tool's command would be.
        """
        alpha = self.window_alpha if self.translucent else 1.0
        # The list loaded the stock menu stylesheet (or its fallback) already;
        # only the tool's own is added.
        if self.keep_visible:
            return window_class(backend, qube.name, parent=self.window,
                                on_close=on_close, alpha=alpha, theme=False)
        window_class(backend, qube.name, alpha=alpha, theme=False)
        self.hide_window()
        return None

    def _get_settings_backend(self):
        """qubes-settings' backend of the same kind as the list's."""
        if self._settings_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_settings.backend import create_backend as create_settings_backend
            kind = "mock" if self.backend.is_demo else "qubesadmin"
            self._settings_backend = create_settings_backend(kind)
        return self._settings_backend

    def _on_settings_closed(self, saved: bool) -> None:
        self.settings_window = None
        if saved:
            # A label or template may have changed.
            self._reload()
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_firewall(self, qube: model.Qube) -> None:
        window_class = _firewall_window_class()
        if window_class is None:
            try:
                self.backend.open_firewall(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            self.hide_window()
            return

        self.firewall_window = self._open_tool(
            window_class, self._get_firewall_backend(), qube, self._on_firewall_closed)

    def _get_firewall_backend(self):
        """qubes-firewall's backend of the same kind as the list's."""
        if self._firewall_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_firewall.backend import create_backend as create_firewall_backend
            kind = "mock" if self.backend.is_demo else "qubesadmin"
            self._firewall_backend = create_firewall_backend(kind)
        return self._firewall_backend

    def _on_firewall_closed(self, _saved: bool) -> None:
        # Nothing the list shows depends on firewall rules: no reload.
        self.firewall_window = None
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_devices(self, qube: model.Qube) -> None:
        window_class = _devices_window_class()
        if window_class is None:
            try:
                self.backend.open_devices(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            self.hide_window()
            return

        backend = self._get_devices_backend()
        if backend.is_demo:
            # Two simulated systems; keep "only while shut down" in step.
            backend.set_states({name: q.state for name, q in self.qubes.items()})
        self.devices_window = self._open_tool(
            window_class, backend, qube, self._on_devices_closed)

    def _get_devices_backend(self):
        """qubes-devices' backend of the same kind as the list's."""
        if self._devices_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_devices.backend import create_backend as create_devices_backend
            kind = "mock" if self.backend.is_demo else "qubesadmin"
            self._devices_backend = create_devices_backend(kind)
        return self._devices_backend

    def _on_devices_closed(self, _saved: bool) -> None:
        # Nothing the list shows depends on PCI devices: no reload.
        self.devices_window = None
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_information(self, qube: model.Qube) -> None:
        window_class = _information_window_class()
        if window_class is None:
            try:
                self.backend.open_information(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            self.hide_window()
            return

        self.information_window = self._open_tool(
            window_class, self._get_information_backend(), qube, self._on_information_closed)

    def _get_information_backend(self):
        """qubes-information's backend of the same kind as the list's."""
        if self._information_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_information.backend import create_backend as create_information_backend
            kind = "mock" if self.backend.is_demo else "qubesadmin"
            self._information_backend = create_information_backend(kind)
        return self._information_backend

    def _on_information_closed(self) -> None:
        # The window only reads: nothing to reload.
        self.information_window = None
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_logs(self, qube: model.Qube) -> None:
        window_class = _logs_window_class()
        if window_class is None:
            try:
                self.backend.open_logs(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            self.hide_window()
            return

        self.logs_window = self._open_tool(
            window_class, self._get_logs_backend(), qube, self._on_logs_closed)

    def _get_logs_backend(self):
        """qubes-logs' backend of the same kind as the list's."""
        if self._logs_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_logs.backend import create_backend as create_logs_backend
            # The logs are files in dom0, not read through qubesadmin.
            kind = "mock" if self.backend.is_demo else "files"
            self._logs_backend = create_logs_backend(kind)
        return self._logs_backend

    def _on_logs_closed(self) -> None:
        # The window only reads: nothing to reload.
        self.logs_window = None
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_backup(self, qube: model.Qube) -> None:
        window_class = _backup_window_class()
        if window_class is None:
            try:
                self.backend.open_backup(qube.name)
            except BackendError as exc:
                self._show_error(str(exc))
                return
            self.hide_window()
            return

        backend = self._get_backup_backend()
        if backend.is_demo:
            # Two simulated systems; keep "Revert only while shut down" in step.
            backend.set_states({name: q.state for name, q in self.qubes.items()})
        self.backup_window = self._open_tool(
            window_class, backend, qube, self._on_backup_closed)

    def _get_backup_backend(self):
        """qubes-backup's backend of the same kind as the list's."""
        if self._backup_backend is None:
            # pylint: disable=import-outside-toplevel
            from qubes_backup.backend import create_backend as create_backup_backend
            kind = "mock" if self.backend.is_demo else "qubesadmin"
            self._backup_backend = create_backup_backend(kind)
        return self._backup_backend

    def _on_backup_closed(self, changed: bool) -> None:
        self.backup_window = None
        if changed:
            # A restore added a qube.
            self._reload()
        GLib.timeout_add(150, self._hide_if_inactive)
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def _open_console(self, qube: model.Qube) -> None:
        try:
            self.backend.open_console(qube.name)
        except BackendError as exc:
            self._show_error(str(exc))
            return
        # The console's terminal takes over; get out of its way.
        self.hide_window()

    def _on_app_launch(self, qube: model.Qube, app: model.App) -> None:
        self.dialog = None
        self._set_dimmed(False)
        try:
            self.backend.start_app(qube.name, app.ident)
        except BackendError as exc:
            failed = str(exc)
        else:
            failed = ""
        if failed or self.keep_visible:
            self.window.present()
            self.search_entry.grab_focus_without_selecting()
            GLib.timeout_add(REFOCUS_MS, self._refocus_list)
        if failed:
            self._show_error(failed)
            return
        # The application takes over; get out of its way.
        self.hide_window()

    def _action_finished(self, name: str) -> None:
        self.pending.pop(name, None)
        self._signature = None
        self._poll()

    def _action_failed(self, name: str, action: str, exc: Exception) -> None:
        self.pending.pop(name, None)
        logger.warning("%s %s failed: %s", action, name, exc)
        self._show_error(f"{action.capitalize()} {name}: {exc}")
        self._signature = None
        self._poll()

    # -- signals ---------------------------------------------------------
    def _on_search_changed(self, *_args) -> None:
        # Searching looks through every qube, halted ones too; clearing the
        # search puts the checkbox back as the user had it.
        searching = bool(self.search_entry.get_text())
        self.show_all_check.handler_block_by_func(self._on_show_all_toggled)
        self.show_all_check.set_active(searching or self.user_show_all)
        self.show_all_check.handler_unblock_by_func(self._on_show_all_toggled)
        self._rebuild(reset_selection=True)

    def _on_show_all_toggled(self, *_args) -> None:
        if not self.search_entry.get_text():
            self.user_show_all = self.show_all_check.get_active()
        self._rebuild()

    def _on_row_activated(self, *_args) -> None:
        self._open_dialog()

    def _on_key_press(self, widget, event) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.quit()
            return True
        if ctrl and event.keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self.show_all_check.set_active(not self.show_all_check.get_active())
            return True
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move_selection(1 if event.keyval == Gdk.KEY_Down else -1)
            return True
        if event.keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self._move_selection(10 if event.keyval == Gdk.KEY_Page_Down else -10)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._open_dialog()
            return True
        if event.keyval == Gdk.KEY_Escape:
            self._escape()
            return True
        # Typing anywhere goes to the search field.
        if not self.search_entry.has_focus() and (
            Gdk.keyval_to_unicode(event.keyval) > 32 or event.keyval == Gdk.KEY_BackSpace
        ):
            self.search_entry.grab_focus_without_selecting()
        return False

    def _escape(self) -> None:
        """Escape clears a query first, then dismisses."""
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        else:
            self.hide_window(dismissed=True)

    def _on_focus_out(self, *_args) -> bool:
        # The action dialog and the tool windows (settings, firewall, devices,
        # information, logs, backup) take focus
        # from the list; that is not a dismissal.
        if not self._covered() and not self.keep_visible:
            self.hide_window()
        return False

    def _on_delete(self, *_args) -> bool:
        """Closing hides the resident instance; it does not quit."""
        self.hide_window(dismissed=True)
        return True

    # -- lifecycle -------------------------------------------------------
    def hide_window(self, dismissed: bool = False) -> None:
        """Close what is open over the list, then hide the list.

        ``dismissed`` is the user closing the list (Escape, the close button);
        it hides even with --keep-visible, which only keeps the list up when
        something else would take its place.
        """
        if self.dialog is not None:
            self.dialog.dismiss()
            self.dialog = None
            self._set_dimmed(False)
        if self.settings_window is not None:
            # Unsaved edits go, as they would with Escape.
            self.settings_window.dismiss()
            self.settings_window = None
        if self.firewall_window is not None:
            # Unsaved rule changes go, as they would with Escape.
            self.firewall_window.dismiss()
            self.firewall_window = None
        if self.devices_window is not None:
            # Unsaved assignments go, as they would with Escape.
            self.devices_window.dismiss()
            self.devices_window = None
        if self.information_window is not None:
            self.information_window.dismiss()
            self.information_window = None
        if self.logs_window is not None:
            self.logs_window.dismiss()
            self.logs_window = None
        if self.backup_window is not None:
            # A backup or restore that runs is not stopped: its window stays,
            # on its own, until it is done.
            self.backup_window.dismiss()
            self.backup_window = None
        if self.keep_visible and not dismissed:
            return
        self.window.hide()

    def _present(self, query: str = "") -> None:
        """Show the default view (running qubes), with ``query`` searched."""
        self.user_show_all = self.default_show_all
        self.show_all_check.set_active(self.default_show_all)
        self.search_entry.set_text(query)
        self._rebuild(reset_selection=True)
        self._reload()
        self.window.show_all()
        self.window.present()
        self.search_entry.grab_focus_without_selecting()

    def do_activate(self, *args, **kwargs):  # pylint: disable=arguments-differ
        """Setup on first activation; afterwards a bare summon toggles."""
        query, self.pending_query = self.pending_query, ""
        qube, self.pending_qube = self.pending_qube, ""

        if not self.primary:
            self.perform_setup()
            self.primary = True
            if not self.start_in_background:
                # The dialog waits for the first load, already under way.
                self._dialog_on_load = qube
                self._present(query)
            return

        self.window.set_keep_above(True)
        if qube:
            if self.dialog is not None:
                self.dialog.dismiss()
                self.dialog = None
                self._set_dimmed(False)
            # _present reloads; the dialog opens on the fresh states.
            self._dialog_on_load = qube
            self._present(query)
        elif query:
            self._present(query)
        elif self.window.is_visible() and not self.keep_visible:
            self.hide_window()
        else:
            self._present("")


def main(argv=None):
    """Entry point. Exit code 6 tells systemd not to restart (no display)."""
    argv = sys.argv if argv is None else argv
    logging.basicConfig(level=logging.INFO)
    if not Gtk.init_check()[0]:
        sys.exit(6)
    sys.exit(DashboardApp().run(argv))


if __name__ == "__main__":
    main()
