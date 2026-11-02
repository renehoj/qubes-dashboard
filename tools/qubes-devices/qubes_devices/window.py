"""The PCI devices window for one qube (GTK 3).

qube Settings' Devices tab as one list: every PCI device dom0 has, a check box
for those assigned to the qube, whether strict reset is on for them, and which
other qubes have them. In qubes-firewall's window: the undecorated, translucent
chrome of qubes-dashboard's action dialog, the stock Qubes menu's stylesheet
underneath, and the launcher's red for focus.

:class:`DevicesWindow` is used two ways, like qubes-firewall's window:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-devices <qube>`` command), on its own.

Assignments can only change while the qube is shut down. For a qube that is on
(and for dom0) the window only shows them.

The window has two pages that share its footer: the device list, and the
warnings shown before a save that has any. Nothing reaches the qube before
Save.

Keyboard, in the list: Up/Down select, Space or Enter assigns or unassigns,
Alt+R turns strict reset off or on, typing filters, Alt+O shows only the
assigned devices, Ctrl+Enter or Ctrl+S saves, Escape clears the filter and then
closes without saving. On the warnings page, Ctrl+Enter saves anyway and
Escape goes back to the list.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-firewall and qubes-dashboard, which mirror ``qubes_menu.appmenu``
(the Qubes OS Project, LGPL-2.1+).
"""

from __future__ import annotations

import html
import importlib.resources
import logging
import threading
from typing import Callable

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-firewall and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import Backend, StaleDevicesError  # noqa: E402

logger = logging.getLogger("qubes-devices")

WINDOW_NAME = "qubes-devices-window"
PANE_CLASS = "devices-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
WINDOW_WIDTH = 980
LIST_HEIGHT = 380

LIST, CONFIRM = "list", "confirm"

HINTS = {
    LIST: "Space assign  ·  Alt+R strict reset  ·  type to filter  ·  Ctrl+↵ save  ·  Esc cancel",
    CONFIRM: "Esc back",
}
READ_ONLY_HINT = "type to filter  ·  Esc close"
#: Footer buttons per page: (left, right). Both keep their Alt mnemonic.
BUTTONS = {
    LIST: ("_Cancel", "_Save"),
    CONFIRM: ("_Back", "Save _anyway"),
}

# Store columns: port_id, assigned, then model.COLUMNS.
COL_PORT, COL_ASSIGNED = 0, 1
COL_CELLS = 2


# -- theme (same as qubes-firewall) --------------------------------------------
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
    """Apply the stock menu's light or dark stylesheet; False if not installed."""
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
    return (importlib.resources.files("qubes_devices") / name).read_text(
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


_styled = False


def install_style(widget: Gtk.Widget, alpha: float = WINDOW_ALPHA,
                  theme: bool = True) -> None:
    """Load the stylesheets, once per process.

    ``theme=False`` skips the stock menu stylesheet (or its fallback): a caller
    that already loaded it for the screen, like qubes-dashboard, passes that.
    """
    global _styled  # pylint: disable=global-statement
    if _styled:
        return
    _styled = True
    if theme and not _load_theme(widget):
        logger.info("qubes_menu not installed; using fallback-theme.css")
        _add_css(_own_css("fallback-theme.css"))
    _add_css(_own_css("devices.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


# -- small helpers -------------------------------------------------------------
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


# -- the window ----------------------------------------------------------------
class DevicesWindow(Gtk.Window):
    """PCI devices of one qube. Loads, lets you edit, saves, closes.

    ``on_close(saved)`` is called once when the window goes away, with
    ``saved`` True when assignments were written. ``devices`` skips loading
    (tests, or a caller that already has them).
    """

    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[bool], None] | None = None,
                 devices: model.QubeDevices | None = None,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Devices: {name}"
        if backend.is_demo:
            title += f" ({backend.name})"
        super().__init__(title=title)
        self.set_name(WINDOW_NAME)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(WINDOW_WIDTH, -1)
        if parent is not None:
            # Over the list, like its action dialog. Not in the taskbar: it is
            # part of the list, which has no entry either.
            self.set_transient_for(parent)
            self.set_modal(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        else:
            self.set_position(Gtk.WindowPosition.CENTER)
        translucent = _prepare_transparency(self)
        install_style(self, alpha if translucent else 1.0, theme=theme)

        self.backend = backend
        self.qube_name = name
        self.devices: model.QubeDevices | None = None
        self.session: model.EditSession | None = None
        self.page = LIST
        self._on_close = on_close
        self._closed = False
        self._busy = False
        self._error: str | None = None

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>Devices · {html.escape(name)}</b>")
        heading.get_style_context().add_class("dialog-title")
        pane.pack_start(heading, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(self.subtitle, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_margin_start(16)
        self.stack.set_margin_end(16)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(False)
        pane.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self._build_list_page(), LIST)
        self.stack.add_named(self._build_confirm_page(), CONFIRM)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = Gtk.Label(label=HINTS[LIST], xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)

        self.cancel_button = Gtk.Button.new_with_mnemonic(BUTTONS[LIST][0])
        self.save_button = Gtk.Button.new_with_mnemonic(BUTTONS[LIST][1])
        for button in (self.cancel_button, self.save_button):
            button.get_style_context().add_class("action")
            bottom.pack_start(button, False, False, 0)
        self.save_button.get_style_context().add_class("suggested")
        self.save_button.set_sensitive(False)
        self.save_button.set_no_show_all(True)
        self.cancel_button.connect("clicked", lambda *_: self.back())
        self.save_button.connect("clicked", lambda *_: self.accept())

        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        # Shown once loaded, not before: the window is centred on the size it
        # has when first shown.
        self.stack.set_sensitive(False)
        if devices is not None:
            self._loaded(devices)
        else:
            run_in_thread(backend.get_devices, name,
                          on_done=self._loaded, on_error=self._load_failed)

    # -- pages -----------------------------------------------------------
    def _build_list_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.note = Gtk.Label(xalign=0)
        self.note.set_line_wrap(True)
        self.note.get_style_context().add_class("note")
        self.note.set_no_show_all(True)
        page.pack_start(self.note, False, False, 0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.set_margin_bottom(6)
        page.pack_start(top, False, False, 0)
        self.filter_entry = Gtk.SearchEntry()
        self.filter_entry.set_placeholder_text("filter")
        self.filter_entry.get_style_context().add_class("filter")
        # "changed", not "search-changed": the list is short, filter at once.
        self.filter_entry.connect("changed", lambda *_: self._refresh())
        top.pack_start(self.filter_entry, True, True, 0)
        self.assigned_only = Gtk.CheckButton.new_with_mnemonic("Assigned _only")
        self.assigned_only.set_can_focus(False)
        self.assigned_only.connect("toggled", lambda *_: self._refresh())
        top.pack_start(self.assigned_only, False, False, 0)

        self.store = Gtk.ListStore(str, bool, *([str] * len(model.COLUMNS)))
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.get_style_context().add_class("devices")
        self.tree.set_enable_search(False)
        self.tree.set_headers_clickable(False)

        self.check_cell = Gtk.CellRendererToggle()
        self.check_cell.set_padding(6, 2)
        self.check_cell.connect("toggled", self._on_check_toggled)
        check = Gtk.TreeViewColumn("", self.check_cell, active=COL_ASSIGNED)
        self.tree.append_column(check)
        for index, title in enumerate(model.COLUMNS):
            cell = Gtk.CellRendererText()
            cell.set_padding(4, 2)
            column = Gtk.TreeViewColumn(title, cell, text=COL_CELLS + index)
            if title in ("Class", "Device", "Used by"):
                cell.set_property("ellipsize", Pango.EllipsizeMode.END)
                column.set_min_width({"Class": 170, "Device": 300, "Used by": 130}[title])
            if title == "Device":
                column.set_expand(True)
            column.set_reorderable(False)
            self.tree.append_column(column)
        self.tree.get_selection().connect("changed", lambda *_: self._update_tools())

        scroller = Gtk.ScrolledWindow()
        scroller.get_style_context().add_class("devices-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(LIST_HEIGHT)
        scroller.add(self.tree)
        page.pack_start(scroller, True, True, 0)

        self.empty = Gtk.Label(label="No matching devices", xalign=0)
        self.empty.get_style_context().add_class("implicit")
        self.empty.set_no_show_all(True)
        page.pack_start(self.empty, False, False, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tools.set_margin_top(6)
        page.pack_start(tools, False, False, 0)
        self.toggle_button = Gtk.Button.new_with_mnemonic("_Assign")
        self.reset_button = Gtk.Button.new_with_mnemonic("Turn strict _reset off")
        for button, callback in ((self.toggle_button, self.toggle_selected),
                                 (self.reset_button, self.toggle_strict_reset)):
            button.get_style_context().add_class("action")
            button.get_style_context().add_class("tool")
            button.connect("clicked", lambda _b, cb=callback: cb())
            tools.pack_start(button, False, False, 0)
        self.tools_box = tools
        return page

    def _build_confirm_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Label(label="Check before saving", xalign=0)
        header.get_style_context().add_class("section")
        page.pack_start(header, False, False, 0)
        self.warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.pack_start(self.warning_box, False, False, 0)
        tail = Gtk.Label(
            label="The devices are attached the next time the qube starts. The "
                  "old assignments are kept in a snapshot file.",
            xalign=0)
        tail.set_line_wrap(True)
        tail.get_style_context().add_class("note")
        page.pack_start(tail, False, False, 0)
        return page

    # -- loading ---------------------------------------------------------
    @property
    def editable(self) -> bool:
        return self.devices is not None and self.devices.editable

    def _loaded(self, devices: model.QubeDevices) -> None:
        if self._closed:
            return
        self.devices = devices
        self.session = model.EditSession(devices)
        if devices.is_admin:
            self._set_note(model.ADMIN_NOTE)
        elif not devices.editable:
            self._set_note(model.RUNNING_NOTE.format(
                name=devices.name, state=devices.state.lower()))
        editable = devices.editable
        self.check_cell.set_activatable(editable)
        self.check_cell.set_sensitive(editable)
        self.save_button.set_visible(editable)
        self.save_button.set_sensitive(editable)
        self.cancel_button.set_label("_Cancel" if editable else "_Close")
        self.stack.set_sensitive(True)
        self._refresh(select=devices.assignments[0].port_id if devices.assignments else None)
        self.show_all()
        self.tools_box.set_visible(editable)
        self.stack.set_visible_child_name(LIST)
        self._set_footer(HINTS[LIST] if editable else READ_ONLY_HINT)
        self.tree.grab_focus()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Could not read the devices of %s: %s", self.qube_name, exc)
        self.subtitle.set_text("")
        self.cancel_button.set_label("_Close")
        self.show_all()
        self.tools_box.hide()
        self._show_error(f"Could not read the devices: {exc}")
        self.cancel_button.grab_focus()

    def _set_note(self, text: str) -> None:
        self.note.set_text(text)
        self.note.show()

    # -- the device list -------------------------------------------------
    def shown_devices(self) -> list[model.PciDevice]:
        query = self.filter_entry.get_text()
        only = self.assigned_only.get_active()
        return [d for d in self.devices.devices
                if model.matches(d, query)
                and (not only or self.session.is_assigned(d.port_id))]

    def _refresh(self, select: str | None = None) -> None:
        """Redraw the list from the working copy; keep or set the selection."""
        if self.session is None:
            return
        if select is None:
            select = self.selected()
        self.store.clear()
        for device in self.shown_devices():
            assignment = self.session.assignment(device.port_id)
            self.store.append([device.port_id, assignment is not None,
                               *model.device_cells(device, assignment)])
        self.empty.set_visible(not len(self.store))
        target = 0
        for row in self.store:
            if row[COL_PORT] == select:
                target = row.path.get_indices()[0]
                break
        if len(self.store):
            path = Gtk.TreePath.new_from_indices([target])
            self.tree.get_selection().select_path(path)
            self.tree.set_cursor(path, None, False)
            self.tree.scroll_to_cell(path, None, False, 0, 0)
        self._update_tools()
        text = model.summary(self.devices, len(self.session.current))
        self.subtitle.set_text(text + (" · not saved" if self.session.dirty else ""))

    def selected(self) -> str | None:
        _store, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        return self.store[it][COL_PORT]

    def _update_tools(self) -> None:
        if self.session is None:
            return
        port_id = self.selected()
        assigned = port_id is not None and self.session.is_assigned(port_id)
        self.toggle_button.set_label("_Unassign" if assigned else "_Assign")
        self.toggle_button.set_sensitive(self.editable and port_id is not None)
        assignment = self.session.assignment(port_id) if port_id else None
        self.reset_button.set_label("Turn strict _reset on" if assignment and
                                    assignment.no_strict_reset else "Turn strict _reset off")
        self.reset_button.set_sensitive(self.editable and assignment is not None)

    def _move_selection(self, step: int) -> None:
        count = len(self.store)
        if not count:
            return
        _store, it = self.tree.get_selection().get_selected()
        index = self.store.get_path(it).get_indices()[0] if it else -1
        index = max(0, min(count - 1, index + step))
        path = Gtk.TreePath.new_from_indices([index])
        self.tree.set_cursor(path, None, False)
        self.tree.scroll_to_cell(path, None, False, 0, 0)

    def _can_edit(self) -> bool:
        return self.editable and self.page == LIST and not self._busy

    def toggle_selected(self) -> None:
        port_id = self.selected()
        if port_id is None or not self._can_edit():
            return
        self.session.toggle(port_id)
        self._refresh(port_id)

    def _on_check_toggled(self, _cell, path: str) -> None:
        if not self._can_edit():
            return
        port_id = self.store[path][COL_PORT]
        self.session.toggle(port_id)
        self._refresh(port_id)

    def toggle_strict_reset(self) -> None:
        port_id = self.selected()
        if port_id is None or not self._can_edit():
            return
        if self.session.toggle_strict_reset(port_id) is not None:
            self._refresh(port_id)

    # -- saving ----------------------------------------------------------
    def save(self) -> None:
        if self._busy or self._closed or self.session is None or not self.editable:
            return
        if not self.session.dirty:
            self._finish(saved=False)
            return
        warnings = model.check_save(self.devices, self.session)
        if warnings:
            self._show_warnings(warnings)
            return
        self._write()

    def _show_warnings(self, warnings: list[str]) -> None:
        for child in self.warning_box.get_children():
            self.warning_box.remove(child)
        for text in warnings:
            label = Gtk.Label(label=f"• {text}", xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(100)
            label.get_style_context().add_class("warning")
            self.warning_box.pack_start(label, False, False, 0)
        self.warning_box.show_all()
        self._show_page(CONFIRM)
        # Back, not Save anyway: Enter twice must not write past the warnings.
        self.cancel_button.grab_focus()

    def _write(self) -> None:
        self._set_busy(True)
        self._set_footer("Saving…")
        logger.info("Saving %d PCI assignment(s) of %s",
                    len(self.session.current), self.qube_name)
        run_in_thread(self.backend.save_assignments, self.qube_name,
                      self.devices.assignments, self.session.assignments(),
                      on_done=self._saved, on_error=self._save_failed)

    def _saved(self, snapshot: str) -> None:
        logger.info("Saved %s; the old assignments are in %s", self.qube_name, snapshot)
        self._finish(saved=True)

    def _save_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Saving %s failed: %s", self.qube_name, exc)
        self._set_busy(False)
        if self.page == CONFIRM:
            self._show_page(LIST)
        if isinstance(exc, StaleDevicesError):
            # Saving again would only fail again.
            self.save_button.set_sensitive(False)
        self._show_error(str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stack.set_sensitive(not busy)
        self.save_button.set_sensitive(not busy)
        self.cancel_button.set_sensitive(not busy)

    # -- pages and footer ------------------------------------------------
    def _show_page(self, page: str) -> None:
        self.page = page
        self.stack.set_visible_child_name(page)
        left, right = BUTTONS[page]
        self.cancel_button.set_label(left)
        self.save_button.set_label(right)
        self._error = None
        self._set_footer(HINTS[page])
        # The stack is not homogeneous: let the window fit the new page.
        self.resize(WINDOW_WIDTH, 1)

    def _set_footer(self, text: str, error: bool = False) -> None:
        self.footer.set_text(text)
        self.footer.set_tooltip_text(text if error else None)
        context = self.footer.get_style_context()
        (context.add_class if error else context.remove_class)("error")

    def _show_error(self, text: str) -> None:
        # Stays until the page changes: an error that vanished before it was
        # read would leave the user guessing.
        self._error = text
        self._set_footer(text, error=True)

    # -- keys and closing ------------------------------------------------
    def _on_key_press(self, _widget, event) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        alt = event.state & Gdk.ModifierType.MOD1_MASK
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self.page == LIST and self.filter_entry.get_text():
                self.filter_entry.set_text("")
                self.tree.grab_focus()
            else:
                self.back()
            return True
        if ctrl and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_s, Gdk.KEY_S):
            self.accept()
            return True
        if self.page != LIST or self.session is None:
            return False
        if alt and key in (Gdk.KEY_o, Gdk.KEY_O):
            self.assigned_only.set_active(not self.assigned_only.get_active())
            return True
        if alt or ctrl:
            return False
        if self.get_focus() is self.filter_entry:
            # The filter has the keys; Up/Down and Enter still work the list.
            if key in (Gdk.KEY_Up, Gdk.KEY_Down):
                self._move_selection(-1 if key == Gdk.KEY_Up else 1)
                self.tree.grab_focus()
                return True
            if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.tree.grab_focus()
                return True
            return False
        if self.get_focus() is self.tree:
            if key in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.toggle_selected()
                return True
            # Typing anywhere else goes to the filter.
            if Gdk.keyval_to_unicode(key) > 32 or key == Gdk.KEY_BackSpace:
                self.filter_entry.grab_focus_without_selecting()
                return self.filter_entry.event(event)
        return False

    def accept(self) -> None:
        """The footer's right button: Save or Save anyway."""
        if self._busy or not self.editable:
            return
        if self.page == LIST:
            self.save()
        else:
            self._write()

    def back(self) -> None:
        """The footer's left button and Escape: back to the list, or close."""
        if self._busy:
            return
        if self.page == LIST:
            self.cancel()
            return
        self._show_page(LIST)
        self._refresh()
        self.tree.grab_focus()

    def _finish(self, saved: bool) -> None:
        if self._closed:
            return
        self._closed = True
        self.destroy()
        if self._on_close is not None:
            self._on_close(saved)

    def cancel(self) -> None:
        """Close without saving. Ignored while a save is running."""
        if not self._busy:
            self._finish(saved=False)

    def dismiss(self) -> None:
        """Close without reporting back (the caller is going away itself)."""
        self._on_close = None
        self._finish(saved=False)
