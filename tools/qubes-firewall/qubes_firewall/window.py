"""The firewall window for one qube (GTK 3).

qvm-firewall-ui's rule model -- the ordered list, where order is evaluation
order, validated as qubesd validates it and checked for lockouts before a save
-- in qubes-settings' window: the undecorated, translucent chrome of
qubes-dashboard's action dialog, the stock Qubes menu's stylesheet underneath,
and the launcher's red for focus.

:class:`FirewallWindow` is used two ways, like qubes-settings' window:

* by qubes-dashboard, in-process, over its list: the resident list opens it
  without a cold start, and gets control back (``on_close``) when it closes;
* by ``frontend.main`` (the ``qubes-firewall <qube>`` command), on its own.

The window has three pages that share its footer: the rule list, the rule
editor, and the warnings shown before a save that has any. Nothing reaches the
qube before Save; the editor only changes the window's working copy.

Keyboard, in the list: Up/Down select, Enter edits, Insert adds, Delete
deletes, Alt+Up/Alt+Down move the rule, Alt + an underlined letter presses a
button, Ctrl+Enter or Ctrl+S saves, Escape closes without saving. In the editor
and on the warnings page, Ctrl+Enter accepts and Escape goes back to the list.

``_is_theme_light``, ``_load_theme`` and ``_prepare_transparency`` are the same
as in qubes-settings and qubes-dashboard, which mirror ``qubes_menu.appmenu``
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
# load. Same rule as qubes-settings and upstream's qubes_menu.appmenu.
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

from . import model  # noqa: E402
from .backend import Backend, StaleRulesError  # noqa: E402

logger = logging.getLogger("qubes-firewall")

WINDOW_NAME = "qubes-firewall-window"
PANE_CLASS = "firewall-pane"
#: Window background opacity; qubes-dashboard's default.
WINDOW_ALPHA = 0.92
ALPHA_PLACEHOLDER = "@ALPHA@"
WINDOW_WIDTH = 700
LIST_HEIGHT = 260

RULES, EDIT, CONFIRM = "rules", "edit", "confirm"

HINTS = {
    RULES: "↵ edit  ·  Ins add  ·  Del delete  ·  Alt+↑↓ move  ·  Ctrl+↵ save  ·  Esc cancel",
    EDIT: "Ctrl+↵ done  ·  Esc back",
    CONFIRM: "Esc back",
}
#: Footer buttons per page: (left, right). Both keep their Alt mnemonic.
BUTTONS = {
    RULES: ("_Cancel", "_Save"),
    EDIT: ("_Back", "_Done"),
    CONFIRM: ("_Back", "Save _anyway"),
}


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
    return (importlib.resources.files("qubes_firewall") / name).read_text(
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
    _add_css(_own_css("firewall.css").replace(ALPHA_PLACEHOLDER, f"{alpha:.3f}"))


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


def _combo(options: list[tuple[str, str]]) -> Gtk.ComboBoxText:
    combo = Gtk.ComboBoxText()
    for combo_id, text in options:
        combo.append(combo_id, text)
    return combo


def _title(text: str, widget: Gtk.Widget) -> Gtk.Label:
    title = Gtk.Label.new_with_mnemonic(text)
    title.set_xalign(0)
    title.set_mnemonic_widget(widget)
    title.get_style_context().add_class("field-title")
    return title


def _entry(placeholder: str = "") -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.set_placeholder_text(placeholder)
    entry.set_hexpand(True)
    entry.set_activates_default(False)
    return entry


# -- the window ----------------------------------------------------------------
class FirewallWindow(Gtk.Window):
    """Firewall rules of one qube. Loads, lets you edit, saves, closes.

    ``on_close(saved)`` is called once when the window goes away, with
    ``saved`` True when rules were written. ``firewall`` skips loading
    (tests, or a caller that already has it).
    """

    def __init__(self, backend: Backend, name: str, *,
                 parent: Gtk.Window | None = None,
                 on_close: Callable[[bool], None] | None = None,
                 firewall: model.QubeFirewall | None = None,
                 alpha: float = WINDOW_ALPHA, theme: bool = True):
        title = f"Firewall: {name}"
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
        self.firewall: model.QubeFirewall | None = None
        self.session: model.EditSession | None = None
        #: The rule the editor is changing; None while it adds one.
        self.editing: int | None = None
        self.page = RULES
        self._on_close = on_close
        self._closed = False
        self._busy = False
        self._error: str | None = None

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class(PANE_CLASS)
        self.add(pane)

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>Firewall · {html.escape(name)}</b>")
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
        self.stack.add_named(self._build_rules_page(), RULES)
        self.stack.add_named(self._build_edit_page(), EDIT)
        self.stack.add_named(self._build_confirm_page(), CONFIRM)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)

        self.footer = Gtk.Label(label=HINTS[RULES], xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)

        self.cancel_button = Gtk.Button.new_with_mnemonic(BUTTONS[RULES][0])
        self.save_button = Gtk.Button.new_with_mnemonic(BUTTONS[RULES][1])
        for button in (self.cancel_button, self.save_button):
            button.get_style_context().add_class("action")
            bottom.pack_start(button, False, False, 0)
        self.save_button.get_style_context().add_class("suggested")
        self.save_button.set_sensitive(False)
        self.cancel_button.connect("clicked", lambda *_: self.back())
        self.save_button.connect("clicked", lambda *_: self.accept())

        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.cancel() or True)

        # Shown once loaded, not before: the window is centred on the size it
        # has when first shown.
        self.stack.set_sensitive(False)
        if firewall is not None:
            self._loaded(firewall)
        else:
            run_in_thread(backend.get_firewall, name,
                          on_done=self._loaded, on_error=self._load_failed)

    # -- pages -----------------------------------------------------------
    def _build_rules_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.note = Gtk.Label(xalign=0)
        self.note.set_line_wrap(True)
        self.note.get_style_context().add_class("note")
        self.note.set_no_show_all(True)
        page.pack_start(self.note, False, False, 0)

        self.store = Gtk.ListStore(*([str] * len(model.COLUMNS)))
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.get_style_context().add_class("rules")
        self.tree.set_enable_search(False)
        self.tree.set_headers_clickable(False)
        for column_index, title in enumerate(model.COLUMNS):
            cell = Gtk.CellRendererText()
            cell.set_padding(4, 2)
            column = Gtk.TreeViewColumn(title, cell, text=column_index)
            if title in ("Destination", "Comment"):
                cell.set_property("ellipsize", Pango.EllipsizeMode.END)
                column.set_expand(True)
                column.set_min_width(120)
            column.set_reorderable(False)
            self.tree.append_column(column)
        self.tree.connect("row-activated", lambda *_: self.edit_rule())
        self.tree.get_selection().connect("changed", lambda *_: self._update_tools())

        scroller = Gtk.ScrolledWindow()
        scroller.get_style_context().add_class("rules-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(LIST_HEIGHT)
        scroller.add(self.tree)
        page.pack_start(scroller, True, True, 0)

        implicit = Gtk.Label(label=model.IMPLICIT_TEXT, xalign=0)
        implicit.get_style_context().add_class("implicit")
        page.pack_start(implicit, False, False, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tools.set_margin_top(6)
        page.pack_start(tools, False, False, 0)
        self.tools: dict[str, Gtk.Button] = {}
        for key, text, callback in (
                ("add", "_Add", self.add_rule),
                ("edit", "_Edit", self.edit_rule),
                ("delete", "_Delete", self.delete_rule),
                ("up", "Move _up", lambda: self.move_rule(-1)),
                ("down", "Move dow_n", lambda: self.move_rule(1))):
            button = Gtk.Button.new_with_mnemonic(text)
            button.get_style_context().add_class("action")
            button.get_style_context().add_class("tool")
            button.connect("clicked", lambda _b, cb=callback: cb())
            tools.pack_start(button, False, False, 0)
            self.tools[key] = button
        return page

    def _build_edit_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.edit_header = Gtk.Label(xalign=0)
        self.edit_header.get_style_context().add_class("section")
        page.pack_start(self.edit_header, False, False, 0)

        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        page.pack_start(grid, False, False, 0)

        self.f_action = _combo([(a, f"{model.ACTION_TEXT[a]} ({a})") for a in model.ACTIONS])
        self.f_dsthost = _entry("any host · example.com · 10.0.0.0/8 · 2001:db8::/32")
        self.f_dns = Gtk.CheckButton.new_with_mnemonic(
            "Only the DNS ser_vers (specialtarget=dns)")
        self.f_proto = _combo([(p, model.PROTOCOL_TEXT[p]) for p in model.PROTOCOLS])
        self.f_ports = _entry("all · 443 · 8000-8080")
        self.f_icmptype = _entry("all · 8 (echo request)")
        self.f_expire = _entry(model.EXPIRE_HINT)
        self.f_comment = _entry("optional")
        self.f_proto.connect("changed", lambda *_: self._update_form_sensitivity())

        rows = (
            ("_Action", self.f_action),
            ("Des_tination", self.f_dsthost),
            (None, self.f_dns),
            ("_Protocol", self.f_proto),
            ("P_orts", self.f_ports),
            ("_ICMP type", self.f_icmptype),
            ("E_xpires", self.f_expire),
            ("Co_mment", self.f_comment),
        )
        self.form_titles: dict[Gtk.Widget, Gtk.Label] = {}
        for row, (text, widget) in enumerate(rows):
            if text is not None:
                title = _title(text, widget)
                self.form_titles[widget] = title
                grid.attach(title, 0, row, 1, 1)
            if isinstance(widget, Gtk.ComboBox):
                widget.set_halign(Gtk.Align.START)
            grid.attach(widget, 1, row, 1, 1)

        self.form_errors = Gtk.Label(xalign=0)
        self.form_errors.set_line_wrap(True)
        self.form_errors.get_style_context().add_class("errors")
        self.form_errors.set_no_show_all(True)
        page.pack_start(self.form_errors, False, False, 0)
        return page

    def _build_confirm_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Label(label="Check before saving", xalign=0)
        header.get_style_context().add_class("section")
        page.pack_start(header, False, False, 0)
        self.warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.pack_start(self.warning_box, False, False, 0)
        tail = Gtk.Label(
            label="The rules apply as soon as they are saved. The old rules are "
                  "kept in a snapshot file.",
            xalign=0)
        tail.set_line_wrap(True)
        tail.get_style_context().add_class("note")
        page.pack_start(tail, False, False, 0)
        return page

    # -- loading ---------------------------------------------------------
    def _loaded(self, firewall: model.QubeFirewall) -> None:
        if self._closed:
            return
        self.firewall = firewall
        self.session = model.EditSession(firewall.rules)
        self.subtitle.set_text(model.summary(firewall))
        if firewall.is_admin:
            self._set_note(model.ADMIN_NOTE)
        elif not firewall.netvm:
            self._set_note(model.NO_NETVM_NOTE)
        self.stack.set_sensitive(not firewall.is_admin)
        self.save_button.set_sensitive(not firewall.is_admin)
        self._refresh(0 if len(firewall.rules) else None)
        self.show_all()
        self.stack.set_visible_child_name(RULES)
        (self.tree if not firewall.is_admin else self.cancel_button).grab_focus()

    def _load_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Could not read the firewall of %s: %s", self.qube_name, exc)
        self.subtitle.set_text("")
        self._show_error(f"Could not read the firewall: {exc}")
        self.show_all()
        self.cancel_button.grab_focus()

    def _set_note(self, text: str) -> None:
        self.note.set_text(text)
        self.note.show()

    # -- the rule list ---------------------------------------------------
    def _refresh(self, select: int | None) -> None:
        """Redraw the list from the working copy and select row ``select``."""
        self.store.clear()
        for index, rule in enumerate(self.session.rules):
            self.store.append(list(model.rule_cells(index, rule)))
        if select is not None and len(self.session.rules):
            path = Gtk.TreePath.new_from_indices([select])
            self.tree.get_selection().select_path(path)
            self.tree.set_cursor(path, None, False)
            self.tree.scroll_to_cell(path, None, False, 0, 0)
        self._update_tools()
        if self.firewall is not None and not self.firewall.is_admin:
            text = model.summary(model.QubeFirewall(
                self.firewall.name, self.firewall.klass, self.firewall.netvm,
                self.session.rules))
            self.subtitle.set_text(text + (" · not saved" if self.session.dirty else ""))

    def selected(self) -> int | None:
        _store, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        return self.store.get_path(it).get_indices()[0]

    def _update_tools(self) -> None:
        if self.session is None:
            return
        index = self.selected()
        count = len(self.session.rules)
        self.tools["add"].set_sensitive(True)
        self.tools["edit"].set_sensitive(index is not None)
        self.tools["delete"].set_sensitive(index is not None)
        self.tools["up"].set_sensitive(index is not None and index > 0)
        self.tools["down"].set_sensitive(index is not None and index < count - 1)

    def delete_rule(self) -> None:
        index = self.selected()
        if index is None or self.page != RULES or self._busy:
            return
        self._refresh(self.session.delete(index))
        self.tree.grab_focus()

    def move_rule(self, step: int) -> None:
        index = self.selected()
        if index is None or self.page != RULES or self._busy:
            return
        move = self.session.move_up if step < 0 else self.session.move_down
        self._refresh(move(index))
        self.tree.grab_focus()

    # -- the editor ------------------------------------------------------
    def add_rule(self) -> None:
        self._open_editor(None)

    def edit_rule(self) -> None:
        index = self.selected()
        if index is not None:
            self._open_editor(index)

    def _open_editor(self, index: int | None) -> None:
        if self.session is None or self.page != RULES or self._busy:
            return
        self.editing = index
        rule = self.session.rules[index] if index is not None else None
        self.edit_header.set_text(f"Rule {index}" if index is not None else "New rule")
        self.set_form(model.form_from_rule(rule))
        self.form_errors.hide()
        self._show_page(EDIT)
        self.f_action.grab_focus()

    def set_form(self, form: model.RuleForm) -> None:
        self.f_action.set_active_id(form.action)
        self.f_dsthost.set_text(form.dsthost)
        self.f_dns.set_active(form.dns)
        self.f_proto.set_active_id(form.proto)
        self.f_ports.set_text(form.dstports)
        self.f_icmptype.set_text(form.icmptype)
        self.f_expire.set_text(form.expire)
        self.f_comment.set_text(form.comment)
        for entry in (self.f_dsthost, self.f_ports, self.f_icmptype,
                      self.f_expire, self.f_comment):
            entry.select_region(0, 0)
        self._update_form_sensitivity()

    def form(self) -> model.RuleForm:
        return model.RuleForm(
            action=self.f_action.get_active_id() or model.ACCEPT,
            dsthost=self.f_dsthost.get_text(),
            dns=self.f_dns.get_active(),
            proto=self.f_proto.get_active_id() or model.ANY,
            dstports=self.f_ports.get_text(),
            icmptype=self.f_icmptype.get_text(),
            expire=self.f_expire.get_text(),
            comment=self.f_comment.get_text(),
        )

    def _update_form_sensitivity(self) -> None:
        form = self.form()
        for widget, enabled in ((self.f_ports, model.ports_enabled(form)),
                                (self.f_icmptype, model.icmptype_enabled(form))):
            widget.set_sensitive(enabled)
            self.form_titles[widget].set_sensitive(enabled)

    def _apply_form(self) -> None:
        rule, problems = model.rule_from_form(self.form())
        if rule is None:
            self.form_errors.set_text("\n".join(problems))
            self.form_errors.show()
            return
        if self.editing is None:
            index = self.session.add(rule, after=self.selected())
        else:
            index = self.session.replace(self.editing, rule)
        self._show_page(RULES)
        self._refresh(index)
        self.tree.grab_focus()

    # -- saving ----------------------------------------------------------
    def save(self) -> None:
        if self._busy or self._closed or self.session is None or self.firewall.is_admin:
            return
        if not self.session.dirty:
            self._finish(saved=False)
            return
        problems, warnings = model.check_save(self.firewall, self.session.rules)
        if problems:
            self._show_error(" ".join(problems))
            return
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
            label.set_max_width_chars(80)
            label.get_style_context().add_class("warning")
            self.warning_box.pack_start(label, False, False, 0)
        self.warning_box.show_all()
        self._show_page(CONFIRM)
        # Back, not Save anyway: Enter twice must not write past the warnings.
        self.cancel_button.grab_focus()

    def _write(self) -> None:
        self._set_busy(True)
        self._set_footer("Saving…")
        logger.info("Saving %d rule(s) to %s", len(self.session.rules), self.qube_name)
        run_in_thread(self.backend.save_rules, self.qube_name,
                      self.session.original, self.session.rules,
                      on_done=self._saved, on_error=self._save_failed)

    def _saved(self, snapshot: str) -> None:
        logger.info("Saved %s; the old rules are in %s", self.qube_name, snapshot)
        self._finish(saved=True)

    def _save_failed(self, exc: Exception) -> None:
        if self._closed:
            return
        logger.warning("Saving %s failed: %s", self.qube_name, exc)
        self._set_busy(False)
        if self.page == CONFIRM:
            self._show_page(RULES)
        if isinstance(exc, StaleRulesError):
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
            self.back()
            return True
        if ctrl and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_s, Gdk.KEY_S):
            self.accept()
            return True
        if self.page != RULES or self.session is None:
            return False
        if alt and key in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.move_rule(-1 if key == Gdk.KEY_Up else 1)
            return True
        if self.get_focus() is self.tree:
            if key in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
                self.delete_rule()
                return True
            if key in (Gdk.KEY_Insert, Gdk.KEY_KP_Insert):
                self.add_rule()
                return True
        return False

    def accept(self) -> None:
        """The footer's right button: Save, Done or Save anyway."""
        if self._busy:
            return
        if self.page == RULES:
            self.save()
        elif self.page == EDIT:
            self._apply_form()
        else:
            self._write()

    def back(self) -> None:
        """The footer's left button and Escape: back to the list, or close."""
        if self._busy:
            return
        if self.page == RULES:
            self.cancel()
            return
        self._show_page(RULES)
        self._refresh(self.editing if self.editing is not None else self.selected())
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
