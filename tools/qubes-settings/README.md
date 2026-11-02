# qubes-settings

A settings window for one qube, for Qubes OS: qubes-manager's settings dialog
in qubes-dashboard's look. It replaces `qubes-vm-settings` as the
**Settings** action in qubes-dashboard, and it also runs on its own:

```console
$ qubes-settings work
```

## Fields

The same as qubes-manager's settings dialog, plus the application menu from
Qubes' Create Qube.

| Section | Field | Notes |
|---|---|---|
| Basic | Label | read-only for dom0 |
| | Template | app and disposable qubes only; only while shut down |
| | Networking | `default (x)`, `(none)` or a qube that provides network |
| | Default disposable | `default (x)`, `(none)` or a disposable template |
| | Include in backups | |
| | Disposable template | app and standalone qubes only |
| Application menu | Applications | which of the template's applications the qube's menu lists |
| Advanced | Initial memory, Max memory | MiB; max 0 disables memory balancing; only while shut down |
| | VCPUs | only while shut down |
| | Virtualization | `default (x)`, pvh, hvm, pv; only while shut down |

dom0 only gets Label (read-only) and Include in backups; it has no
application menu. A field that cannot
be changed in the qube's current state is greyed out, with the reason as its
tooltip, and is never saved. Choosing `default (x)` resets the property, so it
follows the global default again.

## Keys

| key | |
|---|---|
| Tab / Shift+Tab | next / previous field |
| Up / Down | change the focused field (combo boxes, numbers) |
| Alt + underlined letter | jump to that field; Alt+S save, Alt+C cancel |
| Ctrl+Enter, Ctrl+S | save and close |
| Escape | close without saving |

## Application menu

Works like **Select Applications** in Qubes' Create Qube. The settings window
shows the chosen applications as buttons, followed by **+**:

- activating an application's button (Enter, Space or a click) removes it;
- **+** (or Alt+A) opens the list of every application the qube's template
  offers. The chosen ones are highlighted in bold and listed first.

| key | in the list |
|---|---|
| *typing* | searches (name or `.desktop` file name) |
| Up / Down, Page Up / Page Down, Home / End | move the cursor (the outlined row) |
| **Space** (or Enter, or a click) | select / unselect the application under the cursor |
| Escape | clear the search; a second Escape closes the list |
| Ctrl+Enter, **Close and save choice** | close the list |

As in Create Qube, closing the list keeps the choice. Nothing is written until
you save the settings window; its Cancel throws the choice away too.

An application that is in the menu but no longer offered by the template is
listed by its file name at the end, so you can see it and remove it.

In dom0 the list comes from `qvm-appmenus --get-available
--i-understand-format-is-unstable --file-field Comment <qube>`, the call Create
Qube makes (qvm-appmenus in Qubes 4.2 and 4.3 refuses `--get-available`
without that flag; newer versions ignore it), and Save runs `qvm-appmenus
--set-whitelist - --update <qube>`, which sets the `menu-items` feature and
regenerates the menu entries. Where `qvm-appmenus` is not installed (anywhere
but dom0), the section shows why and cannot be changed.

## Saving

Save writes only what changed, and closes. If qubesd refuses a change, the
error is shown in red in the footer, and the window stays open with your edits.

## With qubes-dashboard

When qubes-dashboard can import `qubes_settings` (installed, or a sibling
checkout next to it, see below), **Settings** opens this window in the list's
own process, over the list. The resident list therefore opens it without a
cold start. Closing it returns to the list, with the same qube selected. After
a save, the list reloads, so a new label shows at once.

When `qubes_settings` cannot be imported, the list runs the `qubes-settings`
command if it is on `PATH`, and `qubes-vm-settings` otherwise.

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | `QubeSettings`, which fields a qube has and when they can change, combo contents, what changed |
| `backend/base.py` | `Backend` interface: `get_settings`, `apply_settings` |
| `backend/qubesadmin_backend.py` | The real thing, from qubes-manager |
| `backend/appmenus.py` | `qvm-appmenus` calls and output parsing (no qubesadmin import) |
| `backend/mock.py` | Demo data (qubes-dashboard's set, with demo applications per template) |
| `cli.py` | Headless show and `--set`, for checking the backend in dom0 without a display |
| `window.py` | `SettingsWindow` (GTK 3); used by qubes-dashboard and by `frontend.py` |
| `app_picker.py` | The application list behind **+** |
| `frontend.py` | The `qubes-settings <qube>` command |
| `settings.css` | Window chrome, layered on the stock Qubes menu's stylesheet |
| `fallback-theme.css` | Stand-in colour names for when `qubes_menu` is not installed |

**Design.** The chrome is qubes-dashboard's action dialog: undecorated, kept
above other windows, the stock menu's `qubes-menu-light.css` /
`qubes-menu-dark.css` as the base, the launcher's `#BF616A` red for focus,
and optional translucency (`--alpha`, default 0.92).

**Function.** The data types, the qubesadmin calls and the field rules come
from qubes-manager. The core (model, backend, cli) does not import `gi` or
`qubesadmin` at the top level, so it runs and is tested on any Linux box.

## Running

In dom0 (needs `python3-qubesadmin` and GTK 3, both already there):

```console
$ bin/qubes-settings work                       # from a checkout
$ python3 -m qubes_settings.cli work            # headless: what the window shows
$ python3 -m qubes_settings.cli work --set netvm=sys-vpn --set vcpus=4
$ python3 -m qubes_settings.cli work --set menu_items=firefox-esr.desktop,xterm.desktop
```

`--tab TAB` is accepted and ignored, so `qubes-settings` can stand in wherever
`qubes-vm-settings <qube> --tab TAB` is called.

Anywhere else, `--backend auto` (the default) falls back to demo data, and the
window title says so. `--mock` forces the demo data:

```console
$ bin/qubes-settings --mock office
$ python3 -m qubes_settings.cli --mock office --set netvm=default
```

Tests use the standard library only. The window tests are skipped without GTK 3
or a display:

```console
$ ./run-tests.sh
```

### Getting the files into dom0

Copy it next to qubes-dashboard, the same way:

```console
[user@dom0] $ qvm-run -p <qube> 'tar -C ~/src -cf - qubes-settings --exclude=.git' \
                  | tar -C ~/.local/share -xf -
[user@dom0] $ systemctl --user restart qubes-dashboard
```

qubes-dashboard's `bin/qubes-dashboard` puts a sibling `../qubes-settings`
on `PYTHONPATH`, so with both under `~/.local/share` nothing else is needed.
