# qubes-logs

A qube's logs, in [qubes-dashboard](../..)'s look. Choose
**Logs** in the list's action dialog (key **l**), or run `qubes-logs <qube>`.

It shows the files qubes-manager's log dialog shows, one per tab:

| Tab | File | What is in it |
|---|---|---|
| **Console** | `/var/log/xen/console/guest-<qube>.log` | the qube's serial console: kernel and systemd as it boots, and its login prompt |
| **GUI** | `/var/log/qubes/guid.<qube>.log` | the GUI daemon in dom0 that draws the qube's windows |
| **qrexec** | `/var/log/qubes/qrexec.<qube>.log` | the qrexec daemon: the agent connecting, and calls refused |

dom0 has one tab, **Hypervisor** (`/var/log/xen/console/hypervisor.log`).

Only the last 1 MiB of each file is read (qubes-manager's limit), so the window
opens at once even when a console log has grown for months. The subtitle says
so when that happens. The window opens scrolled to the end, which is the newest
line. It is not refreshed by itself; Ctrl+R reads the files again.

Lines that report an error (*error*, *failed*, *denied*, *panic*,
*traceback*, …) are red. Warnings are amber. Terminal colours and carriage
returns in the console log are removed. A file that is missing or cannot be
read is named in its tab, which is shown in italics, along with the reason.
The window never writes to a log.

## Keys

| key | |
|---|---|
| *typing* | filters the lines: every word must occur in a line, ignoring case. The filter stays when you change tabs |
| Ctrl+1 / Ctrl+2 / Ctrl+3 | Console / GUI / qrexec |
| Ctrl+Tab, Ctrl+Page Down / Ctrl+Shift+Tab, Ctrl+Page Up | next / previous tab |
| Up / Down, Page Up / Page Down | scroll |
| Ctrl+Home / Ctrl+End | top / end |
| Ctrl+R or F5 | read the files again |
| Alt+G | **Copy to global clipboard**: the lines shown (after the filter) |
| Escape | clear the filter; a second Escape closes |

Plain digits go to the filter, so you can search for a time such as `11:38`.
Text can also be selected with the mouse and copied with Ctrl+C.

**Copy to global clipboard** does what Ctrl+Shift+C does in dom0, the same way
qubes-manager does it: it writes `/run/qubes/qubes-clipboard.bin`, with `dom0`
as its source, while holding the GUI daemon's lock. Ctrl+Shift+V in a qube's
window then pastes it there. The text is also put on dom0's own clipboard.

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | which files a qube has; reading the tail of a file; cleaning, filtering and highlighting lines |
| `backend/base.py` | `Backend` interface: `read_logs`, `read_log`, `copy_to_global_clipboard` |
| `backend/files.py` | The real thing: dom0's files, and its global clipboard. Takes a `root` so the tests can use a directory instead of `/` |
| `backend/mock.py` | Demo logs for qubes-dashboard's demo qubes |
| `cli.py` | Headless: prints a log as text |
| `window.py` | `LogsWindow` (GTK 3); the only module besides `frontend.py` importing `gi` |
| `frontend.py` | `qubes-logs <qube>` |
| `logs.css` | Window chrome, layered on the stock Qubes menu's stylesheet |
| `fallback-theme.css` | Stand-in colour names for when `qubes_menu` is not installed |

Like qubes-information, the window is used two ways. qubes-dashboard opens it
in-process over its list (`parent=`, `on_close=`, `theme=False`), and
`frontend.main` opens it on its own.

## Running

In dom0 (needs GTK 3, which is already there):

```console
$ bin/qubes-logs work                                  # from a checkout
$ python3 -m qubes_logs.cli work                       # headless: the console log
$ python3 -m qubes_logs.cli work --log qrexec denied   # ... another log, filtered
$ python3 -m qubes_logs.cli work --list                # the files and their sizes
```

Anywhere else (where `/var/log/xen/console` does not exist), `--backend auto`
(the default) falls back to demo logs. `--mock` forces the demo logs:

```console
$ bin/qubes-logs --mock sys-usb
$ python3 -m qubes_logs.cli --mock sys-usb FAILED
```

Tests use the standard library only. The window tests are skipped without GTK 3
and a display:

```console
$ ./run-tests.sh
```

qubes-dashboard's `packaging/install-on-dom0.sh` installs this repository next
to the list, and `bin/qubes-dashboard` puts it on `PYTHONPATH`.
