"""The settings window for one qube (GTK 3).

qubes-manager's settings dialog -- the same fields, the same "only while shut
down" rules (now in ``model``) -- drawn in qubes-dashboard's look: the
undecorated, translucent chrome of its action dialog, the stock Qubes menu's
stylesheet underneath, and the launcher's red for focus.

:class:`SettingsWindow` is used two ways:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-settings <qube>`` command), on its own.

Keyboard: Tab / Shift+Tab move between fields, Up/Down change the focused
field, Alt + the underlined letter jumps to a field, Ctrl+Enter or Ctrl+S
saves, Escape cancels.

The application menu is Create Qube's: the chosen applications as buttons
(activating one removes it) and a "+" button, Alt+A, that opens
:class:`~qubes_settings.app_picker.AppPicker`.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-dashboard's frontend, which mirrors ``qubes_menu.appmenu`` (the
Qubes OS Project, LGPL-2.1+).
"""

from __future__ import annotations

import html
import importlib.resources
import logging
import threading
from typing import Any, Callable

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-dashboard and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .app_picker import AppPicker  # noqa: E402
from .backend import Backend  # noqa: E402

logger = logging.getLogger("qubes-settings")

WINDOW_NAME = "qubes-settings-window"
PANE_CLASS = "settings-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
LABEL_ICON_SIZE = 12
APP_ICON_SIZE = 16
#: Width the application chips wrap at: the form's second column.
APP_CHIPS_WIDTH = 290

#: Qubes label colours (qubes-core-admin's default labels); qubes-dashboard's.
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

#: Row titles with their Alt mnemonic. Save and Cancel take S and C.
MNEMONIC_TITLES = {
    model.LABEL: "_Label",
    model.TEMPLATE: "_Template",
    model.NETVM: "_Networking",
    model.DEFAULT_DISPVM: "_Default disposable",
    model.INCLUDE_IN_BACKUPS: "Include in _backups",
    model.TEMPLATE_FOR_DISPVMS: "Disposable tem_plate",
    model.MEMORY: "_Initial memory",
    model.MAXMEM: "Ma_x memory",
    model.VCPUS: "_VCPUs",
    model.VIRT_MODE: "Vi_rtualization",
    model.MENU_ITEMS: "_Applications",
}

HINT = "Ctrl+↵ save  ·  Esc cancel"


# -- theme (same as qubes-dashboard) -----------------------------------------
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
    return (importlib.resources.files("qubes_settings") / name).read_text(
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
    _add_css(_own_css("settings.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


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


def label_pixbuf(label: str) -> GdkPixbuf.Pixbuf:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8,
                                  LABEL_ICON_SIZE, LABEL_ICON_SIZE)
    pixbuf.fill((LABEL_COLORS.get(label, 0x8A8A8A) << 8) | 0xFF)
    return pixbuf


def _label_combo() -> Gtk.ComboBox:
    store = Gtk.ListStore(str, GdkPixbuf.Pixbuf)
    for label in model.LABELS:
        store.append([label, label_pixbuf(label)])
    combo = Gtk.ComboBox(model=store)
    combo.set_id_column(0)
    icon = Gtk.CellRendererPixbuf()
    combo.pack_start(icon, False)
    combo.add_attribute(icon, "pixbuf", 1)
    text = Gtk.CellRendererText()
    text.set_padding(6, 0)
    combo.pack_start(text, True)
    combo.add_attribute(text, "text", 0)
    return combo


def _options_combo(options: list[tuple[str, str]]) -> Gtk.ComboBoxText:
    combo = Gtk.ComboBoxText()
    for combo_id, text in options:
        combo.append(combo_id, text)
    for cell in combo.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
    return combo


def app_icon(entry: model.AppEntry) -> Gtk.Image | None:
    """The template's icon for ``entry``, scaled, or None if it has none."""
    if not entry.icon:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            entry.icon, APP_ICON_SIZE, APP_ICON_SIZE, True)
    except GLib.Error:
        return None
    return Gtk.Image.new_from_pixbuf(pixbuf)


def _spin(low: int, high: int, step: int) -> Gtk.SpinButton:
    spin = Gtk.SpinButton.new_with_range(low, high, step)
    spin.set_numeric(True)
    spin.set_width_chars(8)
    spin.set_halign(Gtk.Align.START)
    return spin


# -- the window ----------------------------------------------------------------
class SettingsWindow(Gtk.Window):
    """Settings of one qube. Loads, lets you edit, saves, closes.

    ``on_close(saved)`` is called once when the window goes away, with
    ``saved`` True when settings were written. ``settings`` skips loading
    (tests, or a caller that already has them).
    """

    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[bool], None] | None = None,
                 settings: model.QubeSettings | None = None,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Settings: {name}"
        if backend.is_demo:
            title += f" ({backend.name})"
        super().__init__(title=title)
        self.set_name(WINDOW_NAME)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(460, -1)
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
        self.settings: model.QubeSettings | None = None
        self.fields: dict[str, Gtk.Widget] = {}
        #: The application menu as it stands in the form (.desktop names).
        self.menu_items: list[str] = []
        self.app_box: Gtk.Box | None = None
        self.picker: AppPicker | None = None
        self._on_close = on_close
        self._closed = False
        self._busy = False

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>{html.escape(name)}</b>")
        heading.get_style_context().add_class("dialog-title")
        pane.pack_start(heading, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(self.subtitle, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.set_margin_start(16)
        self.body.set_margin_end(16)
        pane.pack_start(self.body, True, True, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = Gtk.Label(label=HINT, xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)

        self.cancel_button = Gtk.Button.new_with_mnemonic("_Cancel")
        self.save_button = Gtk.Button.new_with_mnemonic("_Save")
        for button in (self.cancel_button, self.save_button):
            button.get_style_context().add_class("action")
            bottom.pack_start(button, False, False, 0)
        self.save_button.get_style_context().add_class("suggested")
        self.save_button.set_sensitive(False)
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        self.save_button.connect("clicked", lambda *_: self.save())

        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        # Shown once loaded, not before: the window is centred on the size it
        # has when first shown, and the form makes it grow downwards.
        if settings is not None:
            self._loaded(settings)
        else:
            run_in_thread(backend.get_settings, name,
                          on_done=self._loaded, on_error=self._load_failed)

    # -- loading ---------------------------------------------------------
    def _loaded(self, settings: model.QubeSettings) -> None:
        if self._closed:
            return
        self.settings = settings
        self.subtitle.set_text(model.summary(settings))
        self._build_form(settings)
        self.save_button.set_sensitive(True)
        self.show_all()
        first = next((w for w in self.fields.values() if w.get_sensitive()),
                     self.save_button)
        # grab_focus, not set_focus: a combo box forwards it to its button.
        first.grab_focus()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Could not read settings of %s: %s", self.qube_name, exc)
        self.subtitle.set_text("")
        self._show_error(f"Could not read settings: {exc}")
        self.show_all()
        self.cancel_button.grab_focus()

    # -- the form --------------------------------------------------------
    def _build_form(self, s: model.QubeSettings) -> None:
        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self.body.pack_start(grid, False, False, 0)
        shown = model.shown_fields(s)
        values = model.initial_values(s)
        row = 0
        for section, fields in (("Basic", model.BASIC_FIELDS),
                                ("Application menu", model.APPLICATION_FIELDS),
                                ("Advanced", model.ADVANCED_FIELDS)):
            fields = [f for f in fields if f in shown]
            if not fields:
                continue
            header = Gtk.Label(label=section, xalign=0)
            header.get_style_context().add_class("section")
            grid.attach(header, 0, row, 2, 1)
            row += 1
            for field in fields:
                widget, child = self._field_widget(s, field, values[field])
                self.fields[field] = widget
                reason = model.disabled_reason(s, field)
                if reason:
                    widget.set_sensitive(False)
                    child.set_tooltip_text(reason)
                if isinstance(widget, Gtk.CheckButton):
                    grid.attach(child, 1, row, 1, 1)
                elif field == model.MENU_ITEMS:
                    title = Gtk.Label.new_with_mnemonic(MNEMONIC_TITLES[field])
                    title.set_xalign(0)
                    title.set_yalign(0)
                    title.set_mnemonic_widget(widget)
                    title.get_style_context().add_class("field-title")
                    title.set_sensitive(widget.get_sensitive())
                    grid.attach(title, 0, row, 1, 1)
                    child.set_hexpand(True)
                    grid.attach(child, 1, row, 1, 1)
                    if s.apps_error:
                        row += 1
                        note = Gtk.Label(label=s.apps_error, xalign=0)
                        note.set_line_wrap(True)
                        note.get_style_context().add_class("note")
                        grid.attach(note, 1, row, 1, 1)
                else:
                    title = Gtk.Label.new_with_mnemonic(MNEMONIC_TITLES[field])
                    title.set_xalign(0)
                    title.set_mnemonic_widget(widget)
                    title.get_style_context().add_class("field-title")
                    title.set_sensitive(widget.get_sensitive())
                    grid.attach(title, 0, row, 1, 1)
                    child.set_hexpand(True)
                    grid.attach(child, 1, row, 1, 1)
                row += 1

        if any(model.disabled_reason(s, f) == model.HALTED_ONLY for f in shown):
            note = Gtk.Label(
                label="Shut the qube down to change the greyed-out settings.",
                xalign=0,
            )
            note.set_line_wrap(True)
            note.get_style_context().add_class("note")
            grid.attach(note, 0, row, 2, 1)

    def _field_widget(self, s: model.QubeSettings, field: str,
                      value: Any) -> tuple[Gtk.Widget, Gtk.Widget]:
        """The input for ``field``, and the widget to place (it may be boxed)."""
        if field == model.LABEL:
            combo = _label_combo()
            combo.set_active_id(value)
            return combo, combo
        if field in (model.TEMPLATE, model.NETVM, model.DEFAULT_DISPVM, model.VIRT_MODE):
            options = {
                model.TEMPLATE: model.template_options,
                model.NETVM: model.netvm_options,
                model.DEFAULT_DISPVM: model.dispvm_options,
                model.VIRT_MODE: model.virt_mode_options,
            }[field](s)
            combo = _options_combo(options)
            combo.set_active_id(value)
            return combo, combo
        if field in (model.INCLUDE_IN_BACKUPS, model.TEMPLATE_FOR_DISPVMS):
            check = Gtk.CheckButton.new_with_mnemonic(MNEMONIC_TITLES[field])
            check.set_active(value)
            return check, check
        if field == model.MENU_ITEMS:
            self.menu_items = list(value)
            # Wrapped by hand, not a FlowBox: a FlowBox asks for the height it
            # would need at its narrowest, and the window keeps that gap.
            self.app_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.app_box.get_style_context().add_class("app-chips")
            self.add_app_button = Gtk.Button(label="+")
            self.add_app_button.get_style_context().add_class("app-chip")
            self.add_app_button.get_style_context().add_class("add")
            self.add_app_button.set_tooltip_text("Choose applications (Alt+A)")
            self.add_app_button.connect("clicked", lambda *_: self.open_picker())
            self._fill_app_box()
            return self.add_app_button, self.app_box
        if field == model.VCPUS:
            spin = _spin(1, model.VCPUS_MAX, 1)
            spin.set_value(value)
            return spin, spin
        # Memory, in MiB.
        spin = _spin(0, model.MEMORY_MAX, 100)
        spin.set_value(value)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(spin, False, False, 0)
        unit = Gtk.Label(label="MiB")
        unit.get_style_context().add_class("unit")
        box.pack_start(unit, False, False, 0)
        if field == model.MAXMEM:
            spin.set_tooltip_text("0 disables memory balancing.")
        return spin, box

    # -- the application menu --------------------------------------------
    def _fill_app_box(self) -> None:
        """Chips for the chosen applications, then "+", wrapped into lines."""
        for line in self.app_box.get_children():
            for chip in line.get_children():
                line.remove(chip)
            self.app_box.remove(line)
        self.chips: list[Gtk.Button] = []
        entries = {e.ident: e for e in model.app_entries(self.settings)}
        editable = model.disabled_reason(self.settings, model.MENU_ITEMS) is None
        for ident in model.ordered_menu_items(self.settings, self.menu_items):
            entry = entries[ident]
            chip = Gtk.Button()
            chip.get_style_context().add_class("app-chip")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            icon = app_icon(entry)
            if icon is not None:
                box.pack_start(icon, False, False, 0)
            box.pack_start(Gtk.Label(label=entry.name), False, False, 0)
            remove = Gtk.Label(label="×")
            remove.get_style_context().add_class("remove")
            box.pack_start(remove, False, False, 0)
            chip.add(box)
            chip.set_tooltip_text(f"{entry.comment}\nActivate to remove from the menu."
                                  if entry.comment else "Activate to remove from the menu.")
            chip.set_sensitive(editable)
            chip.connect("clicked", self._on_chip_clicked, ident)
            self.chips.append(chip)

        line, used = None, 0
        for chip in self.chips + [self.add_app_button]:
            chip.show_all()
            width = chip.get_preferred_width()[1]
            if line is None or used + 4 + width > APP_CHIPS_WIDTH:
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                self.app_box.pack_start(line, False, False, 0)
                used = 0
            line.pack_start(chip, False, False, 0)
            used += width + 4
        self.app_box.show_all()

    def _on_chip_clicked(self, chip: Gtk.Button, ident: str) -> None:
        index = self.chips.index(chip)
        self.menu_items = [i for i in self.menu_items if i != ident]
        self._fill_app_box()
        # Keep the keyboard where it was: on the next chip, or "+".
        (self.chips[index] if index < len(self.chips) else self.add_app_button).grab_focus()

    def open_picker(self) -> None:
        if self.picker is not None or self.settings is None:
            return
        self.picker = AppPicker(self, self.settings, self.menu_items,
                                on_close=self._on_picker_closed,
                                window_name=WINDOW_NAME)

    def _on_picker_closed(self, selected: list[str]) -> None:
        self.picker = None
        self.menu_items = selected
        self._fill_app_box()
        self.present()
        self.add_app_button.grab_focus()

    def values(self) -> dict[str, Any]:
        """What the form holds now, in the shape ``model.changes`` reads."""
        out: dict[str, Any] = {}
        for field, widget in self.fields.items():
            if field == model.MENU_ITEMS:
                out[field] = list(self.menu_items)
            elif isinstance(widget, Gtk.CheckButton):
                out[field] = widget.get_active()
            elif isinstance(widget, Gtk.SpinButton):
                widget.update()
                out[field] = widget.get_value_as_int()
            else:
                out[field] = widget.get_active_id()
        return out

    def changes(self) -> dict[str, Any]:
        if self.settings is None:
            return {}
        return model.changes(self.settings, self.values())

    # -- saving ----------------------------------------------------------
    def save(self) -> None:
        if self._busy or self._closed or self.settings is None:
            return
        changed = self.changes()
        if not changed:
            self._finish(saved=False)
            return
        self._set_busy(True)
        self._set_footer("Saving…")
        logger.info("Saving %s: %s", self.qube_name, changed)
        run_in_thread(self.backend.apply_settings, self.qube_name, changed,
                      on_done=lambda _result: self._finish(saved=True),
                      on_error=self._save_failed)

    def _save_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Saving %s failed: %s", self.qube_name, exc)
        self._set_busy(False)
        self._show_error(str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.body.set_sensitive(not busy)
        self.save_button.set_sensitive(not busy)
        self.cancel_button.set_sensitive(not busy)

    # -- footer ----------------------------------------------------------
    def _set_footer(self, text: str, error: bool = False) -> None:
        self.footer.set_text(text)
        self.footer.set_tooltip_text(text if error else None)
        context = self.footer.get_style_context()
        (context.add_class if error else context.remove_class)("error")

    def _show_error(self, text: str) -> None:
        # Stays until the next save: the window is small, and an error that
        # vanished before it was read would leave the user guessing.
        self._set_footer(text, error=True)

    # -- keys and closing ------------------------------------------------
    def _on_key_press(self, _widget, event) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if event.keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if ctrl and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter,
                                     Gdk.KEY_s, Gdk.KEY_S):
            self.save()
            return True
        return False

    def _finish(self, saved: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if self.picker is not None:
            self.picker.dismiss()
            self.picker = None
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
