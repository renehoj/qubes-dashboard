"""The information window for one qube (GTK 3).

Everything qubesd knows about the qube -- an overview, live memory and uptime
while it runs, every property, feature, service, tag and volume, and its
firewall rules -- on one long page, in qubes-settings' window: the undecorated,
translucent chrome of qubes-dashboard's action dialog, the stock Qubes menu's
stylesheet underneath, and the launcher's red for focus. Nothing in it changes
the qube.

:class:`InformationWindow` is used two ways, like qubes-firewall's window:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-information <qube>`` command), on its own.

Keyboard: typing filters the page (every word must match a row's name, value
or detail, or the section's title); Up/Down and Page Up/Page Down scroll;
Ctrl+1 to Ctrl+8 jump to a section; Ctrl+R or F5 reloads; Alt+C copies the
page as text; Escape clears the filter, then closes.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-settings and qubes-dashboard, which mirror ``qubes_menu.appmenu``
(the Qubes OS Project, LGPL-2.1+).
"""

from __future__ import annotations

import dataclasses
import html
import importlib.resources
import logging
import threading
from typing import Callable

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-settings and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import Backend  # noqa: E402

logger = logging.getLogger("qubes-information")

WINDOW_NAME = "qubes-information-window"
PANE_CLASS = "information-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
WINDOW_WIDTH = 760
PAGE_HEIGHT = 480
#: How often the Live section is refreshed while the qube runs.
STATS_MS = 2000
#: Pixels one Up/Down scrolls.
SCROLL_STEP = 40

HINT = "type to filter  ·  Ctrl+1–8 section  ·  Ctrl+R reload  ·  Esc close"


# -- theme (same as qubes-settings) --------------------------------------------
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
    return (importlib.resources.files("qubes_information") / name).read_text(
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
    _add_css(_own_css("information.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


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


def _label(text: str, *classes: str, selectable: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_valign(Gtk.Align.START)
    if selectable:
        # Selectable so a value can be copied with the mouse; never a Tab
        # stop, so the keyboard stays in the filter field.
        label.set_selectable(True)
        label.set_can_focus(False)
    for name in classes:
        label.get_style_context().add_class(name)
    return label


# -- the window ----------------------------------------------------------------
class InformationWindow(Gtk.Window):
    """Information about one qube. Loads, shows, closes; never writes.

    ``on_close()`` is called once when the window goes away. ``info`` skips
    loading (tests, or a caller that already has it).
    """

    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[], None] | None = None,
                 info: model.QubeInfo | None = None,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Information: {name}"
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
        self.info: model.QubeInfo | None = None
        #: The sections on the page now, after the filter.
        self.shown: list[model.Section] = []
        #: The filter they were drawn with (the field reports changes late).
        self.shown_query: str | None = None
        #: Title -> the section's header widget, for Ctrl+number.
        self.headers: dict[str, Gtk.Widget] = {}
        #: Row key -> value label of the Live section, updated in place.
        self._live_labels: dict[str, tuple[Gtk.Label, Gtk.Label]] = {}
        self._on_close = on_close
        self._closed = False
        self._loading = False
        self._stats_busy = False
        self._error: str | None = None
        self._timer = 0

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>Information · {html.escape(name)}</b>")
        heading.get_style_context().add_class("dialog-title")
        pane.pack_start(heading, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(self.subtitle, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("filter")
        self.search_entry.get_style_context().add_class("filter")
        self.search_entry.set_margin_start(16)
        self.search_entry.set_margin_end(16)
        self.search_entry.connect("search-changed", lambda *_: self._render())
        pane.pack_start(self.search_entry, False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.get_style_context().add_class("page-scroller")
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(PAGE_HEIGHT)
        self.scroller.set_max_content_height(PAGE_HEIGHT)
        self.scroller.set_margin_start(16)
        self.scroller.set_margin_end(16)
        self.scroller.set_margin_top(8)
        pane.pack_start(self.scroller, True, True, 0)

        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.page.get_style_context().add_class("page")
        self.scroller.add(self.page)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = Gtk.Label(label=HINT, xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)

        self.copy_button = Gtk.Button.new_with_mnemonic("_Copy")
        self.copy_button.set_tooltip_text("Copy what the page shows, as text")
        self.reload_button = Gtk.Button.new_with_mnemonic("_Reload")
        self.close_button = Gtk.Button.new_with_mnemonic("C_lose")
        for button in (self.copy_button, self.reload_button, self.close_button):
            button.get_style_context().add_class("action")
            button.set_can_focus(False)
            bottom.pack_start(button, False, False, 0)
        self.close_button.get_style_context().add_class("suggested")
        self.copy_button.connect("clicked", lambda *_: self.copy())
        self.reload_button.connect("clicked", lambda *_: self.reload())
        self.close_button.connect("clicked", lambda *_: self.close_window())

        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.close_window() or True)

        # Shown once loaded, not before: the window is centred on the size it
        # has when first shown.
        if info is not None:
            self._loaded(info)
        else:
            self.reload()

    # -- loading ---------------------------------------------------------
    def reload(self) -> None:
        """Read everything again."""
        if self._loading or self._closed:
            return
        self._loading = True
        self.reload_button.set_sensitive(False)
        if self.info is not None:
            self._set_footer("Reloading…")
        run_in_thread(self.backend.get_info, self.qube_name,
                      on_done=self._loaded, on_error=self._load_failed)

    def _loaded(self, info: model.QubeInfo) -> None:
        if self._closed:
            return
        self._loading = False
        self.reload_button.set_sensitive(True)
        first = self.info is None
        self.info = info
        self.subtitle.set_text(model.summary(info))
        self._error = None
        self._set_footer(HINT)
        self._render()
        self._update_timer()
        if first:
            self.show_all()
            self.search_entry.grab_focus_without_selecting()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        self._loading = False
        self.reload_button.set_sensitive(True)
        logger.warning("Could not read %s: %s", self.qube_name, exc)
        self._show_error(f"Could not read {self.qube_name}: {exc}")
        if self.info is None:
            self.subtitle.set_text("")
            self.show_all()
            self.search_entry.grab_focus_without_selecting()

    # -- live stats ------------------------------------------------------
    def _update_timer(self) -> None:
        running = self.info is not None and self.info.is_running and not self._closed
        if running and not self._timer:
            self._timer = GLib.timeout_add(STATS_MS, self._poll_stats)
        elif not running and self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _poll_stats(self) -> bool:
        if self._closed:
            self._timer = 0
            return False
        if not self._stats_busy and not self._loading:
            self._stats_busy = True
            run_in_thread(self.backend.get_stats, self.qube_name,
                          on_done=self._stats_loaded, on_error=self._stats_failed)
        return True

    def _stats_loaded(self, stats: model.LiveStats | None) -> None:
        self._stats_busy = False
        if self._closed or self.info is None:
            return
        if stats is None:
            # Stopped (or paused) since the page was read: read it all again.
            self.reload()
            return
        self.info = dataclasses.replace(self.info, stats=stats)
        live = next((s for s in model.sections(self.info) if s.title == model.LIVE), None)
        if live is None or {r.key for r in live.rows} != set(self._live_labels):
            self._render()
            return
        for row in live.rows:
            value, detail = self._live_labels[row.key]
            value.set_text(row.value)
            detail.set_text(row.detail)

    def _stats_failed(self, exc: Exception) -> None:
        self._stats_busy = False
        logger.info("Live state of %s not read: %s", self.qube_name, exc)

    # -- the page --------------------------------------------------------
    def _render(self) -> None:
        """Draw the sections that match the filter."""
        if self.info is None:
            return
        query = self.search_entry.get_text()
        self.shown = model.filter_sections(model.sections(self.info), query)
        self.shown_query = query
        for child in self.page.get_children():
            self.page.remove(child)
        self.headers = {}
        self._live_labels = {}

        if not self.shown:
            self.page.pack_start(_label(f"Nothing matches “{query}”", "placeholder"),
                                 False, False, 0)
        for number, section in enumerate(self.shown, start=1):
            self.page.pack_start(self._section_widget(number, section), False, False, 0)
        self.page.show_all()

    def _section_widget(self, number: int, section: model.Section) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("section")
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(section.title)}</b>")
        header.pack_start(title, False, False, 0)
        if section.summary:
            header.pack_start(_label(section.summary, "summary"), False, False, 0)
        if number <= 9:
            header.pack_end(_label(f"Ctrl+{number}", "summary"), False, False, 0)
        box.pack_start(header, False, False, 0)
        self.headers[section.title] = header

        grid = Gtk.Grid(column_spacing=16, row_spacing=3)
        grid.get_style_context().add_class("rows")
        box.pack_start(grid, False, False, 0)
        if not section.rows:
            grid.attach(_label(model.EMPTY_TEXT.get(section.title, ""), "placeholder"),
                        0, 0, 2, 1)
            return box

        for index, row in enumerate(section.rows):
            classes = ["failed"] if row.error else ["dim"] if row.dim else []
            key = _label(row.key, "key", *classes, selectable=True)
            key.set_line_wrap(False)
            key.set_ellipsize(Pango.EllipsizeMode.END)
            key.set_max_width_chars(34)
            key.set_tooltip_text(row.key if len(row.key) > 34 else None)
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            cell.set_hexpand(True)
            value = _label(row.value, "value", *classes, selectable=True)
            value.set_max_width_chars(70)
            cell.pack_start(value, False, False, 0)
            detail = _label(row.detail, "detail", selectable=True)
            detail.set_max_width_chars(70)
            detail.set_no_show_all(not row.detail)
            cell.pack_start(detail, False, False, 0)
            grid.attach(key, 0, index, 1, 1)
            grid.attach(cell, 1, index, 1, 1)
            if section.title == model.LIVE:
                self._live_labels[row.key] = (value, detail)
                detail.set_no_show_all(False)
        return box

    def jump_to(self, number: int) -> None:
        """Scroll section ``number`` (1-based, of those shown) to the top."""
        if not 1 <= number <= len(self.shown):
            return
        header = self.headers[self.shown[number - 1].title]
        coords = header.translate_coordinates(self.page, 0, 0)
        if coords is not None:
            self.scroller.get_vadjustment().set_value(coords[1])

    def scroll(self, pixels: float) -> None:
        adjustment = self.scroller.get_vadjustment()
        top = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(0, min(top, adjustment.get_value() + pixels)))

    def text(self) -> str:
        """The page as the filter shows it, as plain text."""
        return model.as_text(self.shown)

    def copy(self) -> None:
        if not self.shown:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self.text(), -1)
        clipboard.store()
        self._set_footer("Copied to the clipboard")

    # -- footer ----------------------------------------------------------
    def _set_footer(self, text: str, error: bool = False) -> None:
        self.footer.set_text(text)
        self.footer.set_tooltip_text(text if error else None)
        context = self.footer.get_style_context()
        (context.add_class if error else context.remove_class)("error")

    def _show_error(self, text: str) -> None:
        self._error = text
        self._set_footer(text, error=True)

    # -- keys and closing ------------------------------------------------
    def _on_key_press(self, _widget, event) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self.search_entry.get_text():
                self.search_entry.set_text("")
            else:
                self.close_window()
            return True
        if key == Gdk.KEY_F5 or (ctrl and key in (Gdk.KEY_r, Gdk.KEY_R)):
            self.reload()
            return True
        if ctrl and Gdk.KEY_1 <= key <= Gdk.KEY_9:
            self.jump_to(key - Gdk.KEY_0)
            return True
        if key in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.scroll(SCROLL_STEP if key == Gdk.KEY_Down else -SCROLL_STEP)
            return True
        if key in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            page = self.scroller.get_vadjustment().get_page_size() * 0.9
            self.scroll(page if key == Gdk.KEY_Page_Down else -page)
            return True
        if ctrl and key in (Gdk.KEY_Home, Gdk.KEY_End):
            self.scroll(-1e9 if key == Gdk.KEY_Home else 1e9)
            return True
        # Typing anywhere goes to the filter field.
        if not self.search_entry.has_focus() and not ctrl and (
            Gdk.keyval_to_unicode(key) > 32 or key == Gdk.KEY_BackSpace
        ):
            self.search_entry.grab_focus_without_selecting()
        return False

    def close_window(self) -> None:
        """Close and report back."""
        if self._closed:
            return
        self._closed = True
        self._update_timer()
        self.destroy()
        if self._on_close is not None:
            self._on_close()

    def dismiss(self) -> None:
        """Close without reporting back (the caller is going away itself)."""
        self._on_close = None
        self.close_window()
