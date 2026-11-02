"""Choosing the applications in a qube's menu (GTK 3).

Qubes' own Select Applications popup (Create Qube, qubes-desktop-linux-manager's
``new_qube/application_selector.py``, the Qubes OS Project, LGPL-2.1+): an
explanation, a search field, every application the template offers with the
chosen ones highlighted and listed first, and "Close and save choice". Here it
has qubes-settings' look, and it is driven from the keyboard the way
qubes-dashboard is: focus stays in the search field, so typing always
searches, Up/Down move the cursor, and Space (or Enter) selects or unselects
the application under it.
"""

from __future__ import annotations

import html
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango  # noqa: E402

from . import model  # noqa: E402

LIST_HEIGHT = 300
EXPLANATION = ("Select applications that will be listed in the menu for this "
               "qube. This can be changed at any time through its settings.")


class _Row(Gtk.ListBoxRow):
    def __init__(self, entry: model.AppEntry, chosen: bool):
        super().__init__()
        self.entry = entry
        self.set_can_focus(False)
        self.set_tooltip_text("\n".join(
            part for part in (entry.comment, f".desktop filename: {entry.ident}") if part))
        self.label = Gtk.Label()
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.add(self.label)
        self.set_chosen(chosen)

    def set_chosen(self, chosen: bool) -> None:
        self.chosen = chosen
        name = html.escape(self.entry.name)
        self.label.set_markup(f"<b>{name}</b>" if chosen else name)
        context = self.get_style_context()
        (context.add_class if chosen else context.remove_class)("chosen")


class AppPicker(Gtk.Window):
    """Pick menu applications. ``on_close(idents)`` gets the choice.

    There is no cancel, as in Create Qube: closing keeps the choice. The
    settings window's own Cancel throws it away.
    """

    def __init__(self, parent: Gtk.Window, settings: model.QubeSettings,
                 selected: list[str], on_close: Callable[[list[str]], None],
                 window_name: str):
        super().__init__(title=f"Applications: {settings.name}")
        self.set_name(window_name)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(380, -1)
        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen.is_composited() else None
        if visual is not None:
            self.set_visual(visual)
            self.set_app_paintable(True)

        self._on_close = on_close
        self._closed = False

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.get_style_context().add_class("settings-pane")
        self.add(pane)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>Applications</b>  {html.escape(settings.name)}")
        title.get_style_context().add_class("dialog-title")
        pane.pack_start(title, False, False, 0)

        explanation = Gtk.Label(label=EXPLANATION, xalign=0)
        explanation.set_line_wrap(True)
        explanation.set_max_width_chars(48)
        explanation.get_style_context().add_class("dialog-subtitle")
        pane.pack_start(explanation, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("search")
        self.search_entry.get_style_context().add_class("search_entry")
        pane.pack_start(self.search_entry, False, False, 0)

        # Chosen first, then by name -- fixed while the window is open, so a
        # row does not jump away from the cursor when it is toggled.
        chosen = set(selected)
        entries = sorted(model.app_entries(settings),
                         key=lambda e: (e.ident not in chosen, e.name.casefold()))
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.set_can_focus(False)
        self.list_box.get_style_context().add_class("app-list")
        self.rows = [_Row(entry, entry.ident in chosen) for entry in entries]
        for row in self.rows:
            self.list_box.add(row)
        self.list_box.set_filter_func(
            lambda row: model.matches(row.entry, self.search_entry.get_text()))

        self.placeholder = Gtk.Label(label="No matching applications", xalign=0.5)
        self.placeholder.get_style_context().add_class("placeholder")
        self.list_box.set_placeholder(self.placeholder)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_size_request(-1, LIST_HEIGHT)
        self.scroller.get_style_context().add_class("app-scroller")
        self.scroller.add(self.list_box)
        pane.pack_start(self.scroller, True, True, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        bottom.set_margin_bottom(8)
        bottom.set_margin_end(8)
        pane.pack_start(bottom, False, False, 0)
        self.footer = Gtk.Label(xalign=0)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer.get_style_context().add_class("footer")
        bottom.pack_start(self.footer, True, True, 0)
        close = Gtk.Button.new_with_mnemonic("_Close and save choice")
        close.set_can_focus(False)
        close.get_style_context().add_class("action")
        close.get_style_context().add_class("suggested")
        close.connect("clicked", lambda *_: self.close_picker())
        bottom.pack_start(close, False, False, 0)

        self._cursor: _Row | None = None
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", lambda *_: self.close_picker() or True)

        self._move_cursor_to(self._visible()[0] if self.rows else None)
        self._update_footer()
        self.set_focus(self.search_entry)
        self.show_all()

    # -- state -----------------------------------------------------------
    def selected(self) -> list[str]:
        return [row.entry.ident for row in self.rows if row.chosen]

    def _visible(self) -> list[_Row]:
        query = self.search_entry.get_text()
        return [row for row in self.rows if model.matches(row.entry, query)]

    def toggle(self, row: _Row | None = None) -> None:
        row = row or self._cursor
        if row is None:
            return
        row.set_chosen(not row.chosen)
        self._update_footer()

    def _update_footer(self) -> None:
        count = len(self.selected())
        self.footer.set_text(f"{count} selected  ·  Space select  ·  Esc close")

    # -- the cursor ------------------------------------------------------
    def _move_cursor_to(self, row: _Row | None) -> None:
        if self._cursor is not None:
            self._cursor.get_style_context().remove_class("cursor")
        self._cursor = row
        if row is None:
            return
        row.get_style_context().add_class("cursor")
        allocation = row.get_allocation()
        if allocation.height > 1:
            adjustment = self.scroller.get_vadjustment()
            top, bottom = allocation.y, allocation.y + allocation.height
            if top < adjustment.get_value():
                adjustment.set_value(top)
            elif bottom > adjustment.get_value() + adjustment.get_page_size():
                adjustment.set_value(bottom - adjustment.get_page_size())

    def _move_cursor(self, step: int) -> None:
        visible = self._visible()
        if not visible:
            return
        index = visible.index(self._cursor) if self._cursor in visible else -1
        index = max(0, min(len(visible) - 1, index + step))
        self._move_cursor_to(visible[index])

    # -- signals ---------------------------------------------------------
    def _on_search_changed(self, *_args) -> None:
        self.list_box.invalidate_filter()
        visible = self._visible()
        self._move_cursor_to(visible[0] if visible else None)

    def _on_row_activated(self, _list_box, row: _Row) -> None:
        self._move_cursor_to(row)
        self.toggle(row)

    def _on_key_press(self, _widget, event) -> bool:
        keyval = event.keyval
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if keyval == Gdk.KEY_Escape:
            # Like the list: clear the search first, then close.
            if self.search_entry.get_text():
                self.search_entry.set_text("")
            else:
                self.close_picker()
            return True
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.close_picker()
            return True
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.toggle()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move_cursor(1 if keyval == Gdk.KEY_Down else -1)
            return True
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self._move_cursor(10 if keyval == Gdk.KEY_Page_Down else -10)
            return True
        if keyval in (Gdk.KEY_Home, Gdk.KEY_End) and not self.search_entry.get_text():
            visible = self._visible()
            if visible:
                self._move_cursor_to(visible[0 if keyval == Gdk.KEY_Home else -1])
            return True
        if not self.search_entry.has_focus():
            self.search_entry.grab_focus_without_selecting()
        return False

    # -- closing ---------------------------------------------------------
    def close_picker(self) -> None:
        """Close, handing the choice back."""
        if self._closed:
            return
        self._closed = True
        selected = self.selected()
        self.destroy()
        self._on_close(selected)

    def dismiss(self) -> None:
        """Close without reporting back (the settings window is going away)."""
        self._closed = True
        self.destroy()
