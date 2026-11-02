"""The log window for one qube (GTK 3).

The end of the qube's three logs -- the Xen console, the GUI daemon and
qrexec, or Xen's own console for dom0 -- one per tab, in qubes-information's
window: the undecorated, translucent chrome of qubes-dashboard's action
dialog, the stock Qubes menu's stylesheet underneath, and the launcher's red
for focus. Lines that report an error are red, warnings amber. Nothing in it
changes a log.

:class:`LogsWindow` is used two ways, like qubes-information's window:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-logs <qube>`` command), on its own.

Keyboard: typing filters the lines (every word must occur in a line);
Ctrl+1 to Ctrl+3, Ctrl+Tab and Ctrl+Page Up/Down switch log; Up/Down and
Page Up/Page Down scroll; Ctrl+Home/End go to the top or the end; Ctrl+R or F5
reads the files again; Alt+G copies what is shown to the global clipboard;
Escape clears the filter, then closes.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-information and qubes-dashboard, which mirror
``qubes_menu.appmenu`` (the Qubes OS Project, LGPL-2.1+).
"""

from __future__ import annotations

import importlib.resources
import html
import logging
import threading
from typing import Callable

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-information and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import Backend  # noqa: E402

logger = logging.getLogger("qubes-logs")

WINDOW_NAME = "qubes-logs-window"
PANE_CLASS = "logs-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
WINDOW_WIDTH = 900
PAGE_HEIGHT = 520
#: Pixels one Up/Down scrolls.
SCROLL_STEP = 40
#: Pixels a wrapped line's continuation is indented by.
HANGING_INDENT = 24
#: Highlight colours: the launcher's red, and an amber per theme.
ERROR_COLOUR = "#BF616A"
WARNING_COLOUR = {True: "#A0700F", False: "#EBCB8B"}  # by _is_theme_light

HINT = "type to filter  ·  Ctrl+1–3 log  ·  Ctrl+R reload  ·  Esc close"


# -- theme (same as qubes-information) -----------------------------------------
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
    return (importlib.resources.files("qubes_logs") / name).read_text(encoding="utf-8")


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
    _add_css(_own_css("logs.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


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
class LogsWindow(Gtk.Window):
    """The logs of one qube. Loads, shows, closes; never writes a log.

    ``on_close()`` is called once when the window goes away. ``logs`` skips
    loading (tests, or a caller that already has them).
    """

    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[], None] | None = None,
                 logs: list[model.LogText] | None = None,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Logs: {name}"
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
        self.files = model.log_files(name)
        #: The logs as last read, in tab order; None until the first read.
        self.logs: list[model.LogText] | None = None
        #: Index of the tab shown.
        self.current = 0
        #: The lines on the page now, after the filter.
        self.shown: list[str] = []
        #: The filter they were drawn with (the field reports changes late).
        self.shown_query: str | None = None
        self._on_close = on_close
        self._closed = False
        self._loading = False

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>Logs · {html.escape(name)}</b>")
        heading.get_style_context().add_class("dialog-title")
        pane.pack_start(heading, False, False, 0)

        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.subtitle.set_selectable(True)
        self.subtitle.set_can_focus(False)
        self.subtitle.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(self.subtitle, False, False, 0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_start(16)
        top.set_margin_end(16)
        pane.pack_start(top, False, False, 0)

        self.tabs: list[Gtk.Button] = []
        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tab_bar.get_style_context().add_class("tabs")
        for index, file in enumerate(self.files):
            tab = Gtk.Button(label=file.title)
            tab.get_style_context().add_class("tab")
            tab.set_can_focus(False)
            tab.set_tooltip_text(f"{file.path}  (Ctrl+{index + 1})")
            tab.connect("clicked", lambda _b, i=index: self.select(i))
            tab_bar.pack_start(tab, False, False, 0)
            self.tabs.append(tab)
        top.pack_start(tab_bar, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("filter")
        self.search_entry.get_style_context().add_class("filter")
        self.search_entry.connect("search-changed", lambda *_: self._render())
        top.pack_start(self.search_entry, True, True, 0)

        self.stack = Gtk.Stack()
        self.stack.set_margin_start(16)
        self.stack.set_margin_end(16)
        self.stack.set_margin_top(8)
        pane.pack_start(self.stack, True, True, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.get_style_context().add_class("page-scroller")
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(PAGE_HEIGHT)
        self.scroller.set_max_content_height(PAGE_HEIGHT)
        self.stack.add_named(self.scroller, "text")

        self.buffer = Gtk.TextBuffer()
        light = _is_theme_light(self)
        self.buffer.create_tag(model.ERROR, foreground=ERROR_COLOUR)
        self.buffer.create_tag(model.WARNING, foreground=WARNING_COLOUR[light])
        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.get_style_context().add_class("log")
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # A negative indent leaves the first line at the margin and indents
        # the lines it wraps onto, so each log line still starts flush.
        self.view.set_left_margin(8)
        self.view.set_indent(-HANGING_INDENT)
        self.view.set_right_margin(8)
        self.view.set_top_margin(6)
        self.scroller.add(self.view)

        self.placeholder = Gtk.Label(xalign=0.5, yalign=0.5)
        self.placeholder.set_line_wrap(True)
        self.placeholder.get_style_context().add_class("placeholder")
        self.placeholder.set_size_request(-1, PAGE_HEIGHT)
        placeholder_frame = Gtk.Box()
        placeholder_frame.get_style_context().add_class("page-scroller")
        placeholder_frame.pack_start(self.placeholder, True, True, 0)
        self.stack.add_named(placeholder_frame, "placeholder")

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = Gtk.Label(label=HINT, xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)

        self.copy_button = Gtk.Button.new_with_mnemonic("Copy to _global clipboard")
        self.copy_button.set_tooltip_text(
            "Copy the lines shown, so Ctrl+Shift+V pastes them into a qube")
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
        if logs is not None:
            self._loaded(logs)
        else:
            self.reload()

    # -- loading ---------------------------------------------------------
    def reload(self) -> None:
        """Read the files again."""
        if self._loading or self._closed:
            return
        self._loading = True
        self.reload_button.set_sensitive(False)
        if self.logs is not None:
            self._set_footer("Reloading…")
        run_in_thread(self.backend.read_logs, self.qube_name,
                      on_done=self._loaded, on_error=self._load_failed)

    def _loaded(self, logs: list[model.LogText]) -> None:
        if self._closed:
            return
        self._loading = False
        self.reload_button.set_sensitive(True)
        first = self.logs is None
        self.logs = logs
        if first:
            # Open on the first log that has something in it.
            self.current = next((i for i, log in enumerate(logs) if not log.error), 0)
        self._set_footer(HINT)
        self._render()
        if first:
            self.show_all()
            self.search_entry.grab_focus_without_selecting()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        self._loading = False
        self.reload_button.set_sensitive(True)
        logger.warning("Could not read the logs of %s: %s", self.qube_name, exc)
        self._set_footer(f"Could not read the logs of {self.qube_name}: {exc}", error=True)
        if self.logs is None:
            self.show_all()
            self.search_entry.grab_focus_without_selecting()

    # -- the page --------------------------------------------------------
    @property
    def log(self) -> model.LogText | None:
        """The log of the tab shown."""
        return None if self.logs is None else self.logs[self.current]

    def select(self, index: int) -> None:
        """Show tab ``index`` (0-based)."""
        if not 0 <= index < len(self.files) or index == self.current:
            return
        self.current = index
        self._render()

    def _render(self) -> None:
        """Draw the lines of the current log that match the filter, newest
        at the bottom and in view."""
        for index, tab in enumerate(self.tabs):
            context = tab.get_style_context()
            (context.add_class if index == self.current else context.remove_class)("active")
            missing = self.logs is not None and self.logs[index].error is not None
            (context.add_class if missing else context.remove_class)("missing")
        log = self.log
        if log is None:
            return
        self.subtitle.set_text(model.summary(log))
        self.subtitle.set_tooltip_text(model.summary(log))
        query = self.search_entry.get_text()
        lines = log.lines
        self.shown = model.filter_lines(lines, query)
        self.shown_query = query

        if log.error:
            self._show_placeholder(f"{log.file.path}: {log.error}")
        elif not lines:
            self._show_placeholder(f"{log.file.path} is empty")
        elif not self.shown:
            self._show_placeholder(f"Nothing matches “{query}”")
        else:
            self.buffer.set_text(model.as_text(self.shown))
            for number, line in enumerate(self.shown):
                tag = model.severity(line)
                if tag is not None:
                    start = self.buffer.get_iter_at_line(number)
                    end = start.copy()
                    end.forward_to_line_end()
                    self.buffer.apply_tag_by_name(tag, start, end)
            self.stack.set_visible_child_name("text")
            self.scroll_to_end()
        if not self._loading:
            self._set_footer(f"{len(self.shown)} of {len(lines)} lines  ·  {HINT}"
                             if query else HINT)

    def _show_placeholder(self, text: str) -> None:
        self.buffer.set_text("")
        self.placeholder.set_text(text)
        self.stack.set_visible_child_name("placeholder")

    def scroll_to_end(self) -> None:
        # The view lays wrapped lines out in the background; scroll_to_mark
        # waits for that, setting the adjustment now would stop short.
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.view.scroll_to_mark(self.buffer.get_insert(), 0, True, 0, 1)

    def scroll(self, pixels: float) -> None:
        adjustment = self.scroller.get_vadjustment()
        top = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(0, min(top, adjustment.get_value() + pixels)))

    def text(self) -> str:
        """The lines shown, as plain text."""
        return model.as_text(self.shown)

    def copy(self) -> None:
        """Copy the lines shown to dom0's clipboard and the global one."""
        text = self.text()
        if not text:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        lines = len(self.shown)
        run_in_thread(
            self.backend.copy_to_global_clipboard, text,
            on_done=lambda _r: self._set_footer(
                f"Copied {lines} line{'s' if lines != 1 else ''} to the global clipboard"
                "  ·  Ctrl+Shift+V in a qube pastes them"),
            on_error=lambda exc: self._set_footer(str(exc), error=True),
        )

    # -- footer ----------------------------------------------------------
    def _set_footer(self, text: str, error: bool = False) -> None:
        if self._closed:
            return
        self.footer.set_text(text)
        self.footer.set_tooltip_text(text if error else None)
        context = self.footer.get_style_context()
        (context.add_class if error else context.remove_class)("error")

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
            self.select(key - Gdk.KEY_1)
            return True
        if ctrl and key in (Gdk.KEY_Tab, Gdk.KEY_Page_Down):
            self.select((self.current + 1) % len(self.files))
            return True
        if ctrl and key in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up):
            self.select((self.current - 1) % len(self.files))
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
        self.destroy()
        if self._on_close is not None:
            self._on_close()

    def dismiss(self) -> None:
        """Close without reporting back (the caller is going away itself)."""
        self._on_close = None
        self.close_window()
