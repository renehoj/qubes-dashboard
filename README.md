# qubes-dashboard

A keyboard-first list of qubes for Qubes OS: qubes-launcher's window with
qubes-manager's job. One search field and a grid with three columns (Name,
Status, Template). By default the grid shows only the qubes that are on.


Enter on a qube never acts by itself. It opens a small dialog with **Start**,
**Stop**, **Restart**, **Applications**, **Settings**, **Firewall**, **Devices**,
**Information**, **Logs**, **Backup** and **Console**, and you pick from there.

## Keys

| key | in the list |
|---|---|
| *typing* | goes into the search field, wherever focus is |
| Up / Down, Page Up / Page Down | move the selection |
| Enter (or double-click) | open the action dialog for the selected qube |
| Ctrl+H | toggle *All qubes*: show halted qubes too |
| Escape | clear the search; a second Escape hides the window (also with `--keep-visible`) |
| Ctrl+Q | stop the resident process |
| focus loss | hides the window, unless `--keep-visible` |

| key | in the dialog |
|---|---|
| Up / Down, Tab | move between buttons |
| Enter | activate the focused button |
| **s** / **t** / **r** / **a** / **e** / **f** / **d** / **i** / **l** / **b** / **c** | Start / Stop / Restart / Applications / Settings / Firewall / Devices / Information / Logs / Backup / Console (underlined letter) |
| Escape | back to the list |

<img width="643" height="482" alt="qdb-main" src="https://github.com/user-attachments/assets/9d888070-6b26-4039-ac3b-b9a1ae52dfac" />

<img width="652" height="483" alt="qdb-options" src="https://github.com/user-attachments/assets/09396302-7c5d-4c30-a6ae-596ad767b1d4" />

Buttons that make no sense in the qube's current state are greyed out. A paused
qube offers **Resume** instead of Start, and dom0 offers only Settings,
Information and Logs (it has no firewall and no devices to assign, and its
backup is the home directory, which Backup does not handle). Settings,
Firewall, Devices, Information, Logs and Backup are there in every state, also
while an action is running. Console is there only for a running qube with no action
in flight, and Applications for a running or shut-down qube with none.

The initial focus is **Start** when Start is possible, and **Settings**
otherwise. It is never Stop or Restart, so pressing Enter twice on a running
qube opens its settings instead of shutting it down.

**Stop and Restart ask first.** A second dialog takes the action dialog's
place: *Stop work?* with **Stop** and **Cancel**. The focus starts on Cancel,
so Enter there changes nothing; the underlined letter (**t** / **r**) or the
button confirms, **c** or Escape cancels and you are back in the list.

**Start, Stop and Restart.** The window stays open, and the Status column says
*Starting…* / *Stopping…* / *Restarting…* until the action finishes. A qube with
an action in flight stays in the list even if it would otherwise be filtered
out. Errors, such as "other qubes are connected to sys-firewall", are shown in
red in the footer.

Settings, Firewall, Devices, Information, Logs and Backup each open a tool's window,
and the list closes, so the tool is all that is left on screen. The window runs
in the list's own process, so the resident instance opens it at once. With
`--keep-visible` the list stays instead, the window opens over it, and when the
window closes you are back in the list with the same qube selected.

<img width="494" height="688" alt="qdb-settings" src="https://github.com/user-attachments/assets/1917e295-0905-4d7c-8ed2-e0e7c08c2028" />

**Settings** opens [qubes-settings](tools/qubes-settings). It has
qubes-manager's settings (label, template, networking, default disposable,
backups, memory, VCPUs, virtualization) and Create Qube's application menu
selection, in this window's look. Escape closes
it, Ctrl+Enter saves.

When `qubes_settings` cannot be imported, Settings runs the `qubes-settings`
command if it is on `PATH`, else `qubes-vm-settings`.

<img width="744" height="463" alt="qdb-firewall" src="https://github.com/user-attachments/assets/9832e63f-a4cc-4d07-b021-7604b37f19a5" />

**Firewall** opens [qubes-firewall](tools/qubes-firewall): the qube's rules in evaluation order, with Add / Edit / Delete / Move, and
Save after a check for lockouts. Escape closes it, Ctrl+Enter saves. When
`qubes_firewall` cannot be imported, Firewall runs the `qubes-firewall` command.

<img width="1025" height="586" alt="qdb-devices" src="https://github.com/user-attachments/assets/8e6f5327-746a-47d1-b7b8-476ba5a359b0" />

**Devices** opens [qubes-devices](tools/qubes-devices): every PCI device, with a check box for those assigned to the qube (as qube
Settings' Devices tab), strict reset per device, and which other qubes have
each one. Devices can only be changed while the qube is **shut down**; for a
qube that is on, the window shows them read-only. Space assigns, Alt+R turns
strict reset off, typing filters; Save warns about PVH, memory balancing, a
device another qube has, and a GPU without its audio function. Escape closes,
Ctrl+Enter saves. When `qubes_devices` cannot be imported, Devices runs the
`qubes-devices` command.

<img width="799" height="670" alt="qdb-info" src="https://github.com/user-attachments/assets/5275e8c7-33f9-4b47-8c06-7fa9fc03ee85" />

**Information** opens [qubes-information](tools/qubes-information): everything about the qube on one page -- an overview, live
memory and uptime while it runs, every property (`qvm-prefs`), feature
(`qvm-features`), service (`qvm-service`), tag (`qvm-tags`) and volume
(`qvm-volume`), and its firewall rules. It only reads. Typing filters the page,
Escape clears the filter and then closes. When `qubes_information` cannot be
imported, Information runs the `qubes-information` command.

<img width="947" height="709" alt="qdb-logs" src="https://github.com/user-attachments/assets/458df489-a4bc-4141-b6ec-415f48431611" />

**Logs** opens [qubes-logs](tools/qubes-logs): the
qube's console log (`/var/log/xen/console/guest-<qube>.log`), GUI daemon log
(`guid.<qube>.log`) and qrexec log (`qrexec.<qube>.log`) as three tabs
(Ctrl+1/2/3). It shows the last 1 MiB of each, newest at the bottom, with error
lines in red and warnings in amber. dom0 gets the hypervisor log. Typing
filters the lines, Ctrl+R reads the files again, and Alt+G copies what is shown
to the global clipboard. Escape clears the filter and then closes. When
`qubes_logs` cannot be imported, Logs runs the `qubes-logs` command.

<img width="680" height="393" alt="qdb-backup" src="https://github.com/user-attachments/assets/53adee44-24a7-4075-ab32-2c2db69754ef" />

**Backup** opens [qubes-backup](tools/qubes-backup): three tabs (Ctrl+1/2/3).
*Backup* runs `qvm-backup` for the qube alone, to a directory in dom0 or in
another qube (say `sys-usb:/media/usb/backups`); *Restore* runs
`qvm-backup-restore` for the qube alone, from a backup file it lists for you,
and brings it back as a new qube (`work1`) without touching the existing one;
*Revert* runs `qvm-volume revert <qube>:private <revision>`, with the revision
picked from a drop-down, and only for a qube that is shut down. The passphrase
goes to the tools on stdin; where backups go is remembered. Progress and the
tool's output show while it runs, Cancel stops it, and the window cannot be
closed meanwhile; hiding the list leaves a running backup's window open on its
own. After a restore the list reloads. Ctrl+Enter runs the tab's action,
Escape closes. When `qubes_backup` cannot be imported, Backup runs the
`qubes-backup` command.

<img width="642" height="481" alt="qdb-apps" src="https://github.com/user-attachments/assets/4fdc22f1-338f-4675-83f3-2af6864cb2a3" />

**Applications** lists the qube's applications: the ones chosen in its
Settings' Applications tab (the `menu-items` feature), with the names and icons
of their Qubes menu entries. Typing filters the list, Up/Down move, Enter
starts the application with `qvm-run -q -a --service -- <qube>
qubes.StartApp+<app>`, as the Qubes menu does, and the list closes (it stays
with `--keep-visible`). A qube that is shut down is started first. Escape
clears the filter, then goes back to the list.

**Console** runs `qvm-console-dispvm <qube>`: the qube's console in a
disposable qube's terminal, for when its GUI or network is not working. The
list closes. Only a running qube has a console, and dom0 has none.

## Search and status

- **Search** matches the qube name only, ignoring case. Several words must all
  match. Names that start with the first word are listed first. The template
  is not searched, so `debian` does not return every qube built on Debian.
- **"On" qubes** are those in state Running, Paused, Transient (starting),
  Halting or Dying. A qube that is shutting down therefore stays visible until
  it has stopped.
- **Status updates.** While the window is visible it asks qubesd for every power
  state once a second, with a single `admin.vm.List` call, the same approach as
  qubes-manager. When the set of qubes changes (a qube is created or removed, or
  a disposable comes or goes), it reloads the full list. Nothing is polled while
  the window is hidden.

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | `Qube`, filtering/ordering, which actions each state allows |
| `backend/base.py` | `Backend` interface: list, power states, start/stop/restart, settings, firewall, devices, information, logs, backup and console commands, a qube's applications |
| `backend/appmenus.py` | Names and icons of a qube's Qubes menu entries, read from the files qvm-appmenus makes |
| `backend/qubesadmin_backend.py` | The real thing, adapted from qubes-manager |
| `backend/mock.py` | Demo data (qubes-manager's set), with simulated start/stop time |
| `cli.py` | Headless listing, for checking the backend in dom0 without a display |
| `frontend.py` | GTK 3 window, action and applications dialogs; the only module importing `gi`. Opens qubes-settings', qubes-firewall's, qubes-devices', qubes-information's, qubes-logs' and qubes-backup's windows |
| `dashboard.css` | Window chrome, layered on the stock Qubes menu's stylesheet |
| `fallback-theme.css` | Stand-in colour names for when `qubes_menu` is not installed |

**Design.** The chrome is qubes-launcher's:
- an undecorated window, with no taskbar or pager entry, kept above other
  windows;
- the stock menu's `qubes-menu-light.css` / `qubes-menu-dark.css` as the base,
  with the launcher's `#BF616A` red for the search field border and the
  selected row;
- optional translucency (`--alpha`, default 0.92).

**Function.** The backend split, the qubesadmin calls and the demo data come
from qubes-manager. The core (model, backend, cli) does not import `gi` or
`qubesadmin` at the top level, so it runs and is tested on any Linux box.

## Running

In dom0 (needs `python3-qubesadmin`, GTK 3, and ideally
`qubes-desktop-linux-menu` for the stylesheet; all three are already there).
The settings, firewall, devices, information, logs and backup windows are
qubes-settings, qubes-firewall, qubes-devices, qubes-information, qubes-logs
and qubes-backup under `tools/`; `bin/qubes-dashboard` adds them to
`PYTHONPATH`. Each is a project of its own there, with its own `bin/`,
`setup.py`, tests and README, and runs on its own too
(`tools/qubes-settings/bin/qubes-settings work`):

```console
$ bin/qubes-dashboard                  # from a checkout
$ bin/qubes-dashboard --all            # start with halted qubes shown
$ bin/qubes-dashboard --search sys     # start with a search
$ bin/qubes-dashboard --qube work     # start in work's action dialog
$ python3 -m qubes_dashboard.cli       # headless: what the window would list
```

Anywhere else, `--backend auto` (the default) falls back to demo data, and the
footer says so. `--mock` forces the demo data:

```console
$ bin/qubes-dashboard --mock --keep-visible
$ python3 -m qubes_dashboard.cli --mock --all --actions
```

Tests use the standard library only, and the GTK markup tests are skipped
without GTK 3:

```console
$ ./run-tests.sh
```

## Hotkey (resident instance)

This works exactly like qubes-launcher (see its `packaging/hotkey.md`). The bus
name is `org.qubesos.dashboard` instead of `org.qubesos.launcher`. A cold
Python + GTK + qubesadmin start takes about one second. A resident
`--background` instance only shows a window that is already built.

```console
[user@dom0] $ install -Dm644 packaging/qubes-dashboard.service \
                  ~/.config/systemd/user/qubes-dashboard.service
[user@dom0] $ install -Dm644 packaging/dbus-qubes-dashboard.service \
                  ~/.local/share/dbus-1/services/dbus-qubes-dashboard.service
[user@dom0] $ install -Dm644 packaging/autostart/qubes-dashboard.desktop \
                  ~/.config/autostart/qubes-dashboard.desktop
[user@dom0] $ systemctl --user daemon-reload
[user@dom0] $ systemctl --user start qubes-dashboard
```

The unit's `ExecStart=/usr/bin/qubes-dashboard` assumes an installed package
(`pip install .`). To run from a checkout copied into dom0 (see below),
override it with a drop-in that points at `bin/qubes-dashboard` (`%h` is your
home directory):

```console
[user@dom0] $ systemctl --user edit qubes-dashboard
[Service]
ExecStart=
ExecStart=%h/.local/share/qubes-dashboard/bin/qubes-dashboard --background
```

Then bind a key in *XFCE → Keyboard → Application Shortcuts* to:

```
gdbus call --session --dest org.qubesos.dashboard --object-path /org/qubesos/dashboard --method org.freedesktop.Application.Activate "{}"
```

- Pressing the key again hides the window.
- `"{'search': <'sys'>}"` instead of `"{}"` opens the window with that search
  already filled in.
- `"{'qube': <'work'>}"` opens the window with `work` selected and its action
  dialog open (as `--qube work`). A halted qube ticks *All qubes*, so it is
  still in the list when the dialog closes; an unknown name is reported in the
  footer.
- `bin/qubes-dashboard --quit` or `Ctrl+Q` stops the resident process.

### Getting the files into dom0

```console
[user@dom0] $ qvm-run -p <qube> "tar -C ~/src -cf - qubes-dashboard --exclude=.git" \
                  | tar -C ~/.local/share -xf -
```

Then restart the resident instance (`systemctl --user restart
qubes-dashboard`). The running process keeps the code it loaded at start until
it is restarted.
