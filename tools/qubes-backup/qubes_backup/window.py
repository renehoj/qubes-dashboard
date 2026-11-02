"""The backup window for one qube (GTK 3).

Three pages, one per tab -- Backup, Restore and Revert -- in qubes-logs'
window: the undecorated, translucent chrome of qubes-dashboard's action
dialog, the stock Qubes menu's stylesheet underneath, and the launcher's red
for focus. While a backup or restore runs, its progress and the tool's output
are shown under the page, and Cancel stops it.

:class:`BackupWindow` is used two ways, like qubes-logs' window:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-backup <qube>`` command), on its own.

Keyboard: Ctrl+1 to Ctrl+3, Ctrl+Tab and Ctrl+Page Up/Down switch page;
Ctrl+Enter runs the page's action (Back up, Restore, Revert); the underlined
letters (Alt+letter) reach the fields and buttons; Escape closes, except
while something runs. Revert asks first, with the focus on Cancel.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-logs and qubes-dashboard, which mirror ``qubes_menu.appmenu``
(the Qubes OS Project, LGPL-2.1+).
"""

from __future__ import annotations

import html
import importlib.resources
import logging
import os
import threading
from typing import Callable

import gi

# Both namespaces must be pinned, and Gtk must come first: dom0 has GTK4
# installed, so a bare Gdk import resolves to 4.0 and Gtk 3.0 then refuses to
# load. Same rule as qubes-logs and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import Backend, BackendError, Cancelled, Job  # noqa: E402

logger = logging.getLogger("qubes-backup")

WINDOW_NAME = "qubes-backup-window"
PANE_CLASS = "backup-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
WINDOW_WIDTH = 640
#: Height of the tool's output while a backup or restore runs.
LOG_HEIGHT = 180
#: Milliseconds between pulses of the progress bar when the tool gives no figure.
PULSE_INTERVAL = 120

HINT = "Ctrl+1–3 page  ·  Ctrl+Enter run  ·  Esc close"
RUNNING_HINT = "Running…  ·  Cancel stops it"

#: The button that runs each page's action.
RUN_LABELS = {model.BACKUP: "_Back up", model.RESTORE: "_Restore",
              model.REVERT: "Re_vert"}


# -- theme (same as qubes-logs) ------------------------------------------------
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
    return (importlib.resources.files("qubes_backup") / name).read_text(encoding="utf-8")


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
    _add_css(_own_css("backup.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


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


def _class(widget: Gtk.Widget, *names: str) -> Gtk.Widget:
    for name in names:
        widget.get_style_context().add_class(name)
    return widget


def _toggle_class(widget: Gtk.Widget, name: str, on: bool) -> None:
    context = widget.get_style_context()
    (context.add_class if on else context.remove_class)(name)


class _Form(Gtk.Grid):
    """A page's fields: a label with an underlined letter, then the field."""

    def __init__(self):
        super().__init__(column_spacing=12, row_spacing=8)
        _class(self, "form")
        self._row = 0

    def add(self, mnemonic: str, field: Gtk.Widget, target: Gtk.Widget | None = None,
            extra: Gtk.Widget | None = None) -> Gtk.Widget:
        label = Gtk.Label.new_with_mnemonic(mnemonic) if mnemonic else Gtk.Label()
        label.set_xalign(1)
        _class(label, "field-label")
        if mnemonic:
            label.set_mnemonic_widget(target or field)
        self.attach(label, 0, self._row, 1, 1)
        field.set_hexpand(True)
        if extra is None:
            self.attach(field, 1, self._row, 2, 1)
        else:
            self.attach(field, 1, self._row, 1, 1)
            self.attach(extra, 2, self._row, 1, 1)
        self._row += 1
        return field


def _password_entry() -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.set_visibility(False)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    return _class(entry, "field")


def _qube_combo(names: list[str], text: str) -> Gtk.ComboBoxText:
    combo = Gtk.ComboBoxText.new_with_entry()
    for name in names:
        combo.append_text(name)
    combo.get_child().set_text(text)
    _class(combo.get_child(), "field")
    return combo


# -- the window ----------------------------------------------------------------
class BackupWindow(Gtk.Window):
    """Backup, restore and revert for one qube.

    ``on_close(changed)`` is called once when the window goes away; ``changed``
    is True when a restore added a qube. ``settings_path`` is where the last
    destination is remembered (tests pass a temporary file).
    """

    # pylint: disable=too-many-instance-attributes,too-many-statements
    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[bool], None] | None = None,
                 settings_path: str = model.SETTINGS_PATH,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Backup: {name}"
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
        self.settings_path = settings_path
        self.settings = model.load_settings(settings_path)
        #: The qube as last loaded; None until the first load.
        self.info: model.QubeInfo | None = None
        self.revisions: list[model.Revision] = []
        #: The page shown (model.PAGES).
        self.current = model.BACKUP
        #: The backup or restore running, if any.
        self.job: Job | None = None
        #: Something is being done (a job, a revert, a load): the action waits.
        self.busy = False
        #: True once a restore added a qube; passed to on_close.
        self.changed = False
        self._on_close = on_close
        self._closed = False
        self._pulse_source = 0
        self._found_backups_for: tuple[str, str] | None = None
        #: Called (on the GTK thread) when a backup, restore or revert ends;
        #: for tests.
        self.on_finished: Callable[[], None] | None = None

        pane = _class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), PANE_CLASS)
        self.add(pane)

        heading = _class(Gtk.Label(xalign=0), "dialog-title")
        heading.set_markup(f"<b>Backup · {html.escape(name)}</b>")
        pane.pack_start(heading, False, False, 0)

        self.subtitle = _class(Gtk.Label(xalign=0), "dialog-subtitle")
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        pane.pack_start(self.subtitle, False, False, 0)

        tab_bar = _class(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL), "tabs")
        tab_bar.set_margin_start(16)
        tab_bar.set_margin_end(16)
        pane.pack_start(tab_bar, False, False, 0)
        self.tabs: dict[str, Gtk.Button] = {}
        for index, page in enumerate(model.PAGES):
            tab = _class(Gtk.Button(label=model.PAGE_TITLES[page]), "tab")
            tab.set_can_focus(False)
            tab.set_tooltip_text(f"Ctrl+{index + 1}")
            tab.connect("clicked", lambda _b, p=page: self.select(p))
            tab_bar.pack_start(tab, False, False, 0)
            self.tabs[page] = tab

        self.stack = Gtk.Stack()
        self.stack.set_margin_start(16)
        self.stack.set_margin_end(16)
        self.stack.set_margin_top(12)
        self.stack.set_homogeneous(True)
        pane.pack_start(self.stack, False, False, 0)
        self.notes: dict[str, Gtk.Label] = {}
        self.forms: dict[str, Gtk.Widget] = {}
        self._build_backup_page()
        self._build_restore_page()
        self._build_revert_page()

        # Progress and the tool's output: hidden until something runs.
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.progress_box.set_margin_start(16)
        self.progress_box.set_margin_end(16)
        self.progress_box.set_margin_top(12)
        self.progress_box.set_no_show_all(True)
        pane.pack_start(self.progress_box, False, False, 0)
        self.progress = _class(Gtk.ProgressBar(), "progress")
        self.progress.set_show_text(True)
        self.progress_box.pack_start(self.progress, False, False, 0)
        scroller = _class(Gtk.ScrolledWindow(), "page-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(LOG_HEIGHT)
        scroller.set_max_content_height(LOG_HEIGHT)
        self.progress_box.pack_start(scroller, False, False, 0)
        self.log_buffer = Gtk.TextBuffer()
        self.log_buffer.create_tag("command", weight=Pango.Weight.BOLD)
        self.log_view = _class(Gtk.TextView(buffer=self.log_buffer), "log")
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_left_margin(8)
        self.log_view.set_right_margin(8)
        self.log_view.set_top_margin(6)
        self.log_view.set_can_focus(False)
        scroller.add(self.log_view)
        self.progress.show()
        scroller.show_all()

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(12)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = _class(Gtk.Label(label=HINT, xalign=0), "footer")
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        bottom.pack_start(self.footer, True, True, 0)

        # The buttons, or the question before a revert in their place.
        self.bottom_stack = Gtk.Stack()
        self.bottom_stack.set_homogeneous(False)
        bottom.pack_start(self.bottom_stack, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.run_button = _class(Gtk.Button.new_with_mnemonic(RUN_LABELS[model.BACKUP]),
                                 "action", "suggested")
        self.cancel_button = _class(Gtk.Button.new_with_mnemonic("_Cancel"), "action")
        self.close_button = _class(Gtk.Button.new_with_mnemonic("C_lose"), "action")
        for button in (self.run_button, self.cancel_button, self.close_button):
            button.set_can_focus(False)
            buttons.pack_start(button, False, False, 0)
        self.cancel_button.set_no_show_all(True)
        self.run_button.connect("clicked", lambda *_: self.run())
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        self.close_button.connect("clicked", lambda *_: self.close_window())
        self.bottom_stack.add_named(buttons, "buttons")

        confirm = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.confirm_label = _class(Gtk.Label(xalign=1), "confirm")
        confirm.pack_start(self.confirm_label, False, False, 0)
        self.confirm_button = _class(Gtk.Button.new_with_mnemonic("Re_vert"), "action")
        self.confirm_cancel = _class(Gtk.Button.new_with_mnemonic("_Cancel"),
                                     "action", "suggested")
        for button in (self.confirm_button, self.confirm_cancel):
            confirm.pack_start(button, False, False, 0)
        self.confirm_button.connect("clicked", lambda *_: self._revert_confirmed())
        self.confirm_cancel.connect("clicked", lambda *_: self._end_confirm())
        self.bottom_stack.add_named(confirm, "confirm")

        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.close_window() or True)

        # Shown once loaded, not before: the window is centred on the size it
        # has when first shown.
        self.busy = True
        run_in_thread(self._load, on_done=self._loaded, on_error=self._load_failed)

    # -- the pages -------------------------------------------------------
    def _page(self, page: str, form: _Form) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(form, False, False, 0)
        note = _class(Gtk.Label(xalign=0), "note")
        note.set_line_wrap(True)
        note.set_max_width_chars(70)
        box.pack_start(note, False, False, 0)
        self.notes[page] = note
        self.forms[page] = form
        self.stack.add_named(box, page)

    def _build_backup_page(self) -> None:
        form = _Form()
        self.backup_qube = _qube_combo([], self.settings.backup_qube)
        form.add("Back up _to", self.backup_qube, self.backup_qube.get_child())
        self.backup_directory = _class(Gtk.Entry(), "field")
        self.backup_directory.set_text(self.settings.backup_directory)
        form.add("_Directory", self.backup_directory)
        self.backup_passphrase = form.add("_Passphrase", _password_entry())
        self.backup_repeat = form.add("Re_peat", _password_entry())
        self.backup_repeat.connect("activate", lambda *_: self.run())
        self.compress = Gtk.CheckButton.new_with_mnemonic("Co_mpress")
        self.compress.set_active(self.settings.compress)
        form.add("", self.compress)
        self._page(model.BACKUP, form)

    def _build_restore_page(self) -> None:
        form = _Form()
        self.restore_qube = _qube_combo([], self.settings.restore_qube)
        form.add("Backup _in", self.restore_qube, self.restore_qube.get_child())
        self.restore_directory = _class(Gtk.Entry(), "field")
        self.restore_directory.set_text(self.settings.restore_directory)
        form.add("_Directory", self.restore_directory)
        self.restore_file = Gtk.ComboBoxText.new_with_entry()
        _class(self.restore_file.get_child(), "field")
        self.find_button = _class(Gtk.Button.new_with_mnemonic("_Find"), "action")
        self.find_button.set_tooltip_text("List the backups in the directory")
        self.find_button.connect("clicked", lambda *_: self.find_backups(start=True))
        form.add("Backup _file", self.restore_file, self.restore_file.get_child(),
                 extra=self.find_button)
        self.restore_passphrase = form.add("_Passphrase", _password_entry())
        self.restore_passphrase.connect("activate", lambda *_: self.run())
        self.verify_only = Gtk.CheckButton.new_with_mnemonic("Only _verify the backup")
        self.verify_only.connect("toggled", lambda *_: self._update_buttons())
        form.add("", self.verify_only)
        self._page(model.RESTORE, form)

    def _build_revert_page(self) -> None:
        form = _Form()
        volume = _class(Gtk.Label(label=model.VOLUME, xalign=0), "value")
        form.add("Volume", volume)
        self.revision_combo = Gtk.ComboBoxText()
        _class(self.revision_combo, "field")
        form.add("_Revision", self.revision_combo)
        self._page(model.REVERT, form)

    # -- loading ---------------------------------------------------------
    def _load(self):
        info = self.backend.qube_info(self.qube_name)
        storage = self.backend.storage_qubes()
        revisions = [] if info.is_admin else self.backend.revisions(self.qube_name)
        return info, storage, revisions

    def _loaded(self, result) -> None:
        if self._closed:
            return
        info, storage, revisions = result
        first = self.info is None
        self.busy = False
        self._set_info(info, revisions)
        if first:
            others = [q for q in storage if q != self.qube_name]
            for combo in (self.backup_qube, self.restore_qube):
                for name in others:
                    combo.append_text(name)
            self.show_all()
            self._render()
            self._focus_first_field()
        else:
            self._render()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        self.busy = False
        logger.warning("Could not load %s: %s", self.qube_name, exc)
        first = self.info is None
        if first:
            # Shown anyway, with every action off, so the error can be read.
            self.info = model.QubeInfo(self.qube_name, "AdminVM", model.NA)
            self.show_all()
            self._render()
        self._set_footer(f"Could not load {self.qube_name}: {exc}", error=True)
        self._update_buttons()

    def _set_info(self, info: model.QubeInfo, revisions: list[model.Revision]) -> None:
        self.info = info
        self.revisions = revisions
        selected = self.revision_combo.get_active_id()
        self.revision_combo.remove_all()
        for revision in revisions:
            self.revision_combo.append(revision.id, model.revision_label(revision))
        if revisions:
            if selected is None or not self.revision_combo.set_active_id(selected):
                self.revision_combo.set_active(0)
        detail = f"{info.klass} · {info.state.lower()}"
        if self.backend.is_demo:
            detail += f" · {self.backend.name}"
        self.subtitle.set_text(detail)

    def reload_info(self) -> None:
        """Read the qube's state and revisions again (Revert needs them fresh)."""
        if self.busy or self._closed:
            return
        self.busy = True
        self._update_buttons()

        def load():
            info = self.backend.qube_info(self.qube_name)
            return info, [] if info.is_admin else self.backend.revisions(self.qube_name)

        def done(result):
            if self._closed:
                return
            self.busy = False
            self._set_info(*result)
            self._render()

        def failed(exc):
            if self._closed:
                return
            self.busy = False
            self._set_footer(str(exc), error=True)
            self._update_buttons()

        run_in_thread(load, on_done=done, on_error=failed)

    # -- the page shown --------------------------------------------------
    def select(self, page: str) -> None:
        """Show ``page`` (one of model.PAGES)."""
        if page == self.current or self.job is not None or self._confirming:
            return
        self.current = page
        # The last run's output belongs to the page it ran on.
        self.progress_box.hide()
        self._render()
        self._focus_first_field()
        if page == model.REVERT:
            self.reload_info()
        elif page == model.RESTORE and self._found_backups_for is None:
            self.find_backups(start=False)

    def _render(self) -> None:
        for page, tab in self.tabs.items():
            _toggle_class(tab, "active", page == self.current)
        self.stack.set_visible_child_name(self.current)
        if self.info is not None:
            for page in model.PAGES:
                self.notes[page].set_text(model.page_note(page, self.info))
            if self.current == model.REVERT and not self.revisions \
                    and model.REVERT in self.available:
                self.notes[model.REVERT].set_text(
                    f"{self.qube_name}'s {model.VOLUME} volume has no older revisions "
                    "to revert to.")
        self._update_buttons()

    def _focus_first_field(self) -> None:
        if self.current == model.BACKUP:
            fields = [self.backup_qube.get_child(), self.backup_directory,
                      self.backup_passphrase, self.backup_repeat]
        elif self.current == model.RESTORE:
            fields = [self.restore_qube.get_child(), self.restore_directory,
                      self.restore_file.get_child(), self.restore_passphrase]
        else:
            self.revision_combo.grab_focus()
            return
        empty = next((f for f in fields if not f.get_text()), fields[-1])
        empty.grab_focus()

    @property
    def available(self) -> set[str]:
        return model.available_pages(self.info) if self.info is not None else set()

    def _update_buttons(self) -> None:
        running = self.job is not None
        label = RUN_LABELS[self.current]
        if self.current == model.RESTORE and self.verify_only.get_active():
            label = "_Verify"
        self.run_button.set_label(label)
        can_run = (self.current in self.available and not self.busy
                   and not (self.current == model.REVERT and not self.revisions))
        self.run_button.set_sensitive(can_run)
        self.run_button.set_visible(not running)
        self.cancel_button.set_visible(running)
        self.close_button.set_sensitive(not running)
        editable = not self.busy and self.info is not None and not self.info.is_admin
        for page, form in self.forms.items():
            form.set_sensitive(editable and page in self.available)
        self.find_button.set_sensitive(editable)
        for tab in self.tabs.values():
            tab.set_sensitive(not running)

    # -- running ---------------------------------------------------------
    def run(self) -> None:
        """Run the shown page's action: start a backup or restore, or ask
        before a revert."""
        if not self.run_button.get_sensitive() or not self.run_button.get_visible():
            return
        try:
            if self.current == model.BACKUP:
                self._start_backup()
            elif self.current == model.RESTORE:
                self._start_restore()
            else:
                self._ask_revert()
        except model.InvalidRequest as exc:
            self._set_footer(str(exc), error=True)

    def _start_backup(self) -> None:
        request = model.BackupRequest(
            qube=self.qube_name,
            dest_qube=self.backup_qube.get_active_text().strip(),
            directory=self.backup_directory.get_text().strip(),
            passphrase=self.backup_passphrase.get_text(),
            compress=self.compress.get_active(),
        )
        model.check_backup(request, self.backup_repeat.get_text())
        self.settings.backup_qube = request.dest_qube
        self.settings.backup_directory = request.directory
        self.settings.compress = request.compress
        self._save_settings()
        self._start_job(model.backup_command(request), self.backend.backup, request,
                        f"Backing up {self.qube_name}…", self._backup_done)

    def _backup_done(self, request: model.BackupRequest) -> None:
        self.backup_passphrase.set_text("")
        self.backup_repeat.set_text("")
        self._set_footer(f"{self.qube_name} is backed up.")
        # The new file is there to restore from now.
        if (self.restore_qube.get_active_text().strip(),
                self.restore_directory.get_text().strip()) == \
                (request.dest_qube, request.directory):
            self._found_backups_for = None

    def _restore_request(self) -> model.RestoreRequest:
        directory = self.restore_directory.get_text().strip()
        name = self.restore_file.get_active_text().strip()
        if not name:
            raise model.InvalidRequest("Enter the backup file, or Find the backups.")
        path = name if name.startswith("/") else os.path.join(directory, name)
        return model.RestoreRequest(
            qube=self.qube_name,
            source_qube=self.restore_qube.get_active_text().strip(),
            path=path,
            passphrase=self.restore_passphrase.get_text(),
            verify_only=self.verify_only.get_active(),
        )

    def _start_restore(self) -> None:
        request = self._restore_request()
        model.check_restore(request)
        self.settings.restore_qube = request.source_qube
        self.settings.restore_directory = model.split_path(request.path)[0]
        self._save_settings()
        what = "Checking the backup of" if request.verify_only else "Restoring"
        self._start_job(model.restore_command(request), self.backend.restore, request,
                        f"{what} {self.qube_name}…", self._restore_done)

    def _restore_done(self, request: model.RestoreRequest) -> None:
        self.restore_passphrase.set_text("")
        if request.verify_only:
            self._set_footer("The backup is good: it can be restored.")
        else:
            self.changed = True
            self._set_footer(f"{self.qube_name} is restored, as a new qube.")

    def _start_job(self, argv, method, request, footer, on_done) -> None:
        self.job = job = Job()
        self.busy = True
        self.log_buffer.set_text("")
        self._append_log("$ " + model.command_text(argv), "command")
        self.progress.set_fraction(0)
        self.progress.set_text("")
        self.progress_box.show()
        self._pulse_source = GLib.timeout_add(PULSE_INTERVAL, self._pulse)
        self._set_footer(f"{footer}  ·  Cancel stops it")
        self._update_buttons()

        def report(output: model.Output):
            GLib.idle_add(self._show_output, job, output)

        run_in_thread(method, request, job, report,
                      on_done=lambda _r: self._job_ended(job, request, on_done, None),
                      on_error=lambda exc: self._job_ended(job, request, on_done, exc))

    def _show_output(self, job: Job, output: model.Output) -> bool:
        if self._closed or job is not self.job:
            return False
        if output.percent is not None:
            # A figure: stop pulsing, show it.
            self._stop_pulse()
            self.progress.set_fraction(output.percent / 100)
            self.progress.set_text(f"{output.percent:.0f}%")
        if output.line is not None:
            self._append_log(output.line)
        return False

    def _pulse(self) -> bool:
        self.progress.pulse()
        return True

    def _stop_pulse(self) -> None:
        if self._pulse_source:
            GLib.source_remove(self._pulse_source)
            self._pulse_source = 0

    def _append_log(self, line: str, tag: str | None = None) -> None:
        end = self.log_buffer.get_end_iter()
        text = line if self.log_buffer.get_char_count() == 0 else "\n" + line
        if tag:
            self.log_buffer.insert_with_tags_by_name(end, text, tag)
        else:
            self.log_buffer.insert(end, text)
        self.log_buffer.place_cursor(self.log_buffer.get_end_iter())
        self.log_view.scroll_to_mark(self.log_buffer.get_insert(), 0, True, 0, 1)

    def _job_ended(self, job: Job, request, on_done, error: Exception | None) -> None:
        # Flush the output reported just before the end first.
        GLib.idle_add(self._finish_job, job, request, on_done, error)

    def _finish_job(self, job: Job, request, on_done, error: Exception | None) -> bool:
        if job is not self.job:
            return False
        self.job = None
        self.busy = False
        self._stop_pulse()
        if error is None:
            self.progress.set_fraction(1)
            self.progress.set_text("Done")
            if not self._closed:
                on_done(request)
        elif isinstance(error, Cancelled):
            self.progress.set_text("Cancelled")
            self._append_log("Cancelled.")
            self._set_footer("Cancelled.", error=True)
        else:
            self.progress.set_text("Failed")
            logger.warning("%s", error)
            self._set_footer(str(error), error=True)
        if self._closed:
            return False
        self._update_buttons()
        self._focus_first_field()
        if self.on_finished is not None:
            self.on_finished()
        return False

    def cancel(self) -> None:
        """Stop the backup or restore that runs."""
        if self.job is not None:
            self._set_footer("Cancelling…")
            self.cancel_button.set_sensitive(False)
            self.job.cancel()
            GLib.timeout_add(500, lambda: self.cancel_button.set_sensitive(True) and False)

    # -- revert ----------------------------------------------------------
    @property
    def _confirming(self) -> bool:
        return self.bottom_stack.get_visible_child_name() == "confirm"

    def _ask_revert(self) -> None:
        revision = self._selected_revision()
        if revision is None:
            raise model.InvalidRequest("Pick a revision.")
        when = model.revision_label(revision).split("  (")[0]
        self.confirm_label.set_markup(
            f"<b>Revert {html.escape(self.qube_name)} to {html.escape(when)}?</b>")
        self._set_footer("What was written since then is lost.", error=True)
        self.bottom_stack.set_visible_child_name("confirm")
        self.forms[model.REVERT].set_sensitive(False)
        # Cancel has the focus, so Enter changes nothing.
        self.confirm_cancel.set_can_focus(True)
        self.confirm_button.set_can_focus(True)
        self.confirm_cancel.grab_focus()

    def _selected_revision(self) -> model.Revision | None:
        selected = self.revision_combo.get_active_id()
        return next((r for r in self.revisions if r.id == selected), None)

    def _end_confirm(self, footer: str = HINT) -> None:
        self.bottom_stack.set_visible_child_name("buttons")
        self.confirm_cancel.set_can_focus(False)
        self.confirm_button.set_can_focus(False)
        self._set_footer(footer)
        self._update_buttons()
        self.revision_combo.grab_focus()

    def _revert_confirmed(self) -> None:
        revision = self._selected_revision()
        self._end_confirm(f"Reverting {self.qube_name}…")
        if revision is None:
            return
        self.busy = True
        self._update_buttons()
        run_in_thread(self.backend.revert, self.qube_name, revision.id,
                      on_done=lambda _r: self._reverted(revision, None),
                      on_error=lambda exc: self._reverted(revision, exc))

    def _reverted(self, revision: model.Revision, error: Exception | None) -> None:
        if self._closed:
            return
        self.busy = False
        if error is None:
            when = model.revision_label(revision).split("  (")[0]
            self._set_footer(f"{self.qube_name} is reverted to {when}.")
            self.reload_info()
        else:
            message = str(error) if isinstance(error, BackendError) else repr(error)
            self._set_footer(f"Could not revert {self.qube_name}: {message}", error=True)
            self._update_buttons()
        if self.on_finished is not None:
            self.on_finished()

    # -- finding backup files --------------------------------------------
    def find_backups(self, start: bool) -> None:
        """List qvm-backup's files in the restore directory. Only ``start``
        (the Find button) starts a qube that is off."""
        qube = self.restore_qube.get_active_text().strip()
        directory = self.restore_directory.get_text().strip()
        if not model.is_qube_name(qube) or not directory.startswith("/"):
            if start:
                self._set_footer("Pick the qube and the directory the backups are in.",
                                 error=True)
            return
        self._found_backups_for = (qube, directory)
        self.find_button.set_sensitive(False)
        if start:
            self._set_footer(f"Looking in {qube}:{directory}…")

        def done(names: list[str]):
            if self._closed:
                return
            self.find_button.set_sensitive(not self.busy)
            typed = self.restore_file.get_active_text()
            self.restore_file.remove_all()
            for name in names:
                self.restore_file.append_text(name)
            if names:
                self.restore_file.set_active(0)
                self._set_footer(f"{len(names)} backup{'s' if len(names) != 1 else ''} "
                                 f"in {qube}:{directory}, newest first")
            else:
                self.restore_file.get_child().set_text(typed)
                self._set_footer(f"No backups in {qube}:{directory}", error=start)

        def failed(exc: Exception):
            if self._closed:
                return
            self.find_button.set_sensitive(not self.busy)
            # Looking on its own (the page opened) does not shout.
            self._set_footer(f"Could not list {qube}:{directory}: {exc}", error=start)

        run_in_thread(self.backend.list_backups, qube, directory, start,
                      on_done=done, on_error=failed)

    # -- settings and footer ---------------------------------------------
    def _save_settings(self) -> None:
        try:
            model.save_settings(self.settings, self.settings_path)
        except OSError as exc:
            logger.warning("Could not save %s: %s", self.settings_path, exc)

    def _set_footer(self, text: str, error: bool = False) -> None:
        if self._closed:
            return
        self.footer.set_text(text)
        self.footer.set_tooltip_text(text if error else None)
        # Not "error": the Qubes GTK themes give that class a red background.
        _toggle_class(self.footer, "failed", error)

    # -- keys and closing ------------------------------------------------
    def _on_key_press(self, _widget, event) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self._confirming:
                self._end_confirm()
            elif self.job is not None:
                self._set_footer(RUNNING_HINT)
            else:
                self.close_window()
            return True
        if self._confirming:
            if key in (Gdk.KEY_v, Gdk.KEY_V) and not ctrl:
                self._revert_confirmed()
                return True
            if key in (Gdk.KEY_c, Gdk.KEY_C) and not ctrl:
                self._end_confirm()
                return True
            return False
        if ctrl and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.run()
            return True
        if ctrl and Gdk.KEY_1 <= key <= Gdk.KEY_9:
            index = key - Gdk.KEY_1
            if index < len(model.PAGES):
                self.select(model.PAGES[index])
            return True
        index = model.PAGES.index(self.current)
        if ctrl and key in (Gdk.KEY_Tab, Gdk.KEY_Page_Down):
            self.select(model.PAGES[(index + 1) % len(model.PAGES)])
            return True
        if ctrl and key in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up):
            self.select(model.PAGES[(index - 1) % len(model.PAGES)])
            return True
        return False

    def close_window(self) -> None:
        """Close and report back; not while a backup or restore runs."""
        if self._closed:
            return
        if self.job is not None:
            self._set_footer(RUNNING_HINT)
            return
        self._closed = True
        self._stop_pulse()
        self.destroy()
        if self._on_close is not None:
            self._on_close(self.changed)

    def dismiss(self) -> None:
        """Close without reporting back (the caller is going away itself).

        A backup or restore that runs is not stopped behind the user's back:
        the window stays, on its own, until it is done and closed.
        """
        self._on_close = None
        if self.job is not None:
            self.set_modal(False)
            self.set_transient_for(None)
            self.set_skip_taskbar_hint(False)
            return
        self.close_window()
