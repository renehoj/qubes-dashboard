# qubes-backup

Backup, restore and revert for one qube, in qubes-dashboard's look. It is
qubes-dashboard's **Backup** action (**b** in the action dialog), and the
`qubes-backup <qube>` command on its own.

The window has three pages, one per tab:

| page | runs | what it does |
|---|---|---|
| **Backup** | `qvm-backup` | backs up this qube alone, to a directory in dom0 or in another qube (such as `sys-usb:/media/usb/backups`) |
| **Restore** | `qvm-backup-restore` | restores this qube alone from a backup file, **as a new qube**: `work` comes back as `work1` (`--rename-conflicting`), and the existing `work` is not touched. *Only verify the backup* checks it without restoring (`--verify-only`) |
| **Revert** | `qvm-volume revert` | puts the qube's **private** volume back to an older revision, picked from a drop-down (newest first, with when it was taken). Only for a qube that is **shut down**. Asks first |

The stock tools do the work, so a backup made here is the same as one made by
hand, and `qvm-backup-restore` or the Restore Qubes tool can read it.

- **The passphrase** goes to the tool on stdin (`--passphrase-file -`), never
  on a command line or to disk. Backup asks for it twice.
- **Remembered** between runs, in `~/.config/qubes-backup/settings.json`: the
  qube and directory backups went to and came from, and *Compress*. Never the
  passphrase.
- **A running qube** can be backed up; the backup has its data as of its last
  shutdown (that is how `qvm-backup` works), and the page says so.
- **Backup files.** Restore lists qvm-backup's files
  (`qubes-backup-YYYY-MM-DDThhmmss`) in the directory, newest first: by itself
  when the page opens and the qube is running, or with **Find**, which starts
  the qube if it is off. The listing comes from that qube, so only names in
  qvm-backup's format are shown. A file name can also be typed.
- **Progress.** While a backup or restore runs, a progress bar (qvm-backup's
  percentage; a pulse for restore, which gives none) and the tool's output are
  shown under the page, starting with the command line. **Cancel** sends
  SIGINT, which qvm-backup turns into a cancel in qubesd. The window cannot be
  closed while something runs.
- **dom0** has no Backup button in qubes-dashboard: its backup is the home
  directory, which this window does not handle.

## Keys

| key | |
|---|---|
| Ctrl+1 / Ctrl+2 / Ctrl+3, Ctrl+Tab, Ctrl+Page Up/Down | Backup / Restore / Revert |
| Ctrl+Enter | run the page's action (Enter in the last passphrase field too) |
| Alt+*underlined letter* | go to that field or press that button |
| Escape | close; while it runs, it says that Cancel stops it |

Before a revert, *Revert work to 2026-10-25 14:02?* takes the buttons' place
with the focus on **Cancel**, so Enter changes nothing; **v** or the button
reverts, **c** or Escape does not.

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | Pages per state, the requests and their checks, the command lines, the tools' output, revisions, settings |
| `backend/base.py` | `Backend` interface; `Job` (cancel) |
| `backend/tools.py` | Runs a tool with the passphrase on stdin and its output reported as it comes |
| `backend/qubesadmin_backend.py` | The real thing: qubesadmin for the qubes, the tools for the work |
| `backend/mock.py` | Demo data (qubes-dashboard's qubes), with simulated time and output |
| `cli.py` | Headless check, for the backend in dom0 without a display |
| `window.py` | GTK 3 window; `BackupWindow` is what qubes-dashboard opens |
| `frontend.py` | The `qubes-backup <qube>` command |
| `backup.css` | Window chrome, layered on the stock Qubes menu's stylesheet |
| `fallback-theme.css` | Stand-in colour names for when `qubes_menu` is not installed |

The core (model, backend, cli) does not import `gi` or `qubesadmin` at the top
level, so it runs and is tested on any Linux box.

## Running

In dom0, from a checkout:

```console
$ bin/qubes-backup work
$ python3 -m qubes_backup.cli work --commands     # headless: state, revisions, commands
$ python3 -m qubes_backup.cli work --backups sys-usb:/media/usb/backups
```

Anywhere else, `--backend auto` (the default) falls back to demo data.
`--mock` forces it. In demo mode the passphrase `wrong` makes a restore fail:

```console
$ bin/qubes-backup --mock vault
```

Tests use the standard library only; the window tests are skipped without
GTK 3 and a display:

```console
$ ./run-tests.sh
```

qubes-dashboard opens the window in-process when this checkout is next to
it (its `bin/qubes-dashboard` puts it on `PYTHONPATH`), and its
`packaging/install-on-dom0.sh` installs it along with the others.
