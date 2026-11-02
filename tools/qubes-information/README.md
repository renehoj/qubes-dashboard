# qubes-information

Everything qubesd knows about one qube, on one page, in
[qubes-dashboard](../..)'s look. Choose **Information** in the
list's action dialog (key **i**), or run `qubes-information <qube>`.

The window only reads. To change things, use Settings
([qubes-settings](../qubes-settings)) or Firewall
([qubes-firewall](../qubes-firewall)) from the same dialog, or the `qvm-*`
tools.

## What it shows

| Section | Contents | Where it comes from |
|---|---|---|
| **Overview** | class, state, label, template, net qube, IP/MAC, default disposable, OS and its end of life, updates, memory, VCPUs, virtualization and kernel, disk use, backups, flags (disposable template, autostart, internal, installed by package, debug), UUID | properties and features below |
| **Live** | uptime and start time, memory right now and its ceiling, CPU time, Xen domain ID; refreshed every 2 seconds | `admin.vm.CurrentState` (only while the qube runs) |
| **Properties** | every property, defaults dimmed and marked; dates, MiB and seconds spelled out | `qvm-prefs` |
| **Features** | every feature except services | `qvm-features` |
| **Services** | each `service.*` feature, enabled or disabled | `qvm-service` |
| **Tags** | every tag | `qvm-tags` |
| **Storage** | each volume: used of size, pool, read-only or not, snapshot source, saved or discarded on stop, revisions kept and saved, outdated, volume ID | `qvm-volume info` |
| **Firewall** | the rules in evaluation order, as `qvm-firewall --raw` prints them, and what happens to the rest | `qvm-firewall` |

dom0 has no Storage or Firewall section, and a qube that is not running has no
Live section. If one part cannot be read (an Admin API policy refuses it, or
qubesd is older), that section says why in red and the rest is still shown.

## Keys

| key | |
|---|---|
| *typing* | filters the page: every word must occur in a row's name, value or detail, or in the section's title (`features gui`, `services`, `backup`) |
| Up / Down, Page Up / Page Down | scroll |
| Ctrl+Home / Ctrl+End | top / bottom |
| Ctrl+1 … Ctrl+8 | jump to a section (its number is on the right of its header) |
| Ctrl+R or F5 | read everything again |
| Alt+C | **Copy** what the page shows, as text |
| Escape | clear the filter; a second Escape closes |

Values can also be selected and copied with the mouse.

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | `QubeInfo` and its parts; turns them into the page's sections and rows; filtering; plain-text form |
| `backend/base.py` | `Backend` interface: `get_info`, `get_stats` |
| `backend/qubesadmin_backend.py` | The real thing. Properties come in one `admin.vm.property.GetAll` (qubesadmin's cache); every other part is read on its own, so a refused call empties one section only |
| `backend/mock.py` | Demo data for qubes-dashboard's demo qubes |
| `cli.py` | Headless: prints the page as text |
| `window.py` | `InformationWindow` (GTK 3); the only module besides `frontend.py` importing `gi` |
| `frontend.py` | `qubes-information <qube>` |
| `information.css` | Window chrome, layered on the stock Qubes menu's stylesheet |
| `fallback-theme.css` | Stand-in colour names for when `qubes_menu` is not installed |

Like qubes-firewall, the window is used two ways: qubes-dashboard opens it
in-process over its list (`parent=`, `on_close=`, `theme=False`), and
`frontend.main` opens it on its own.

## Running

In dom0 (needs `python3-qubesadmin` and GTK 3, already there):

```console
$ bin/qubes-information work                    # from a checkout
$ python3 -m qubes_information.cli work         # headless: the page as text
$ python3 -m qubes_information.cli work backup  # ... filtered
```

Anywhere else `--backend auto` (the default) falls back to demo data, and
`--mock` forces it:

```console
$ bin/qubes-information --mock work
$ python3 -m qubes_information.cli --mock sys-net services
```

Tests use the standard library only; the window tests are skipped without GTK 3
and a display:

```console
$ ./run-tests.sh
```

qubes-dashboard's `packaging/install-on-dom0.sh` installs this repository next
to the list, and `bin/qubes-dashboard` puts it on `PYTHONPATH`.
