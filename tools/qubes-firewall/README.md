# qubes-firewall

A firewall window for one qube, for Qubes OS: qvm-firewall-ui's rule model in
qubes-settings' window. It is the **Firewall** action in
[qubes-dashboard](../..), and it also runs on its own:

```console
$ qubes-firewall work
```

## What it shows

The qube's rules in **evaluation order**: the first rule that matches decides,
and what no rule matches is blocked (the last line of the list says so). The
order is the one `qvm-firewall <qube> list` numbers from 0.

| Column | |
|---|---|
| # | position in the list, from 0 |
| Action | Allow (`accept`) or Block (`drop`) |
| Destination | host name, IPv4/IPv6 address or network, *DNS servers* (`specialtarget=dns`), or *any* |
| Protocol | any, TCP, UDP or ICMP |
| Port / type | port or port range (TCP, UDP), ICMP type |
| Expires | local time the rule stops applying, or *expired* |
| Comment | |

A qube without a net qube keeps its rules; they take effect when it gets one,
and the window says so. dom0 has no firewall, and its window is read-only.

## Editing

Nothing reaches the qube before **Save**. Add, Edit, Delete and Move change the
window's working copy; the subtitle says *not saved* while it differs.

**Add** and **Edit** open the rule editor in the same window. A new rule goes
below the selected one. The editor checks the rule the way qubesd will
(qvm-firewall-ui's `validate`), and shows what is wrong instead of accepting it.
Ports are only used for TCP and UDP, the ICMP type only for ICMP; the other
field is greyed out. **Expires** takes `+30m`, `+2h`, `+1d` (or plain seconds,
`+300`) from now, or a local date and time like `2026-12-31 18:00`; empty means
never.

**Save** checks the whole list first (qvm-firewall-ui's `safety`):

- a rule that blocks everything, or the net qube's DNS addresses, before
  anything is allowed (the qube loses its network);
- DNS blocked before rules that name hosts (those can no longer be resolved);
- rules after an *allow everything* rule (they never match);
- expired rules and duplicates.

With any of these, Save shows them and asks again; **Save anyway** writes,
**Back** returns to the list. The focus starts on Back. *Allow some things,
then block everything* — the shape qube Settings writes — is not a warning.

Then it writes, like qvm-firewall-ui's guarded write path:

1. re-reads the qube's rules, and stops, writing nothing, if another tool
   changed them since the window opened;
2. saves the current rules to `~/.local/state/qubes-firewall/<qube>.<UTC time>.rules`,
   in `qvm-firewall --raw` form;
3. writes the whole list with `admin.vm.firewall.Set`, through qubesadmin's own
   `firewall.Rule` objects (as `qvm-firewall` does). Rules apply at once.

If qubesd refuses, the error names the snapshot file, and the window stays
open with your edits.

## Keys

| key | in the list |
|---|---|
| Up / Down | select a rule |
| Enter (or double-click) | edit the rule |
| Insert | add a rule |
| Delete | delete the rule |
| Alt+Up / Alt+Down | move the rule |
| Alt + underlined letter | Add, Edit, Delete, Move up, Move down, Save, Cancel |
| Ctrl+Enter, Ctrl+S | save and close |
| Escape | close without saving |

| key | in the editor and the warnings |
|---|---|
| Tab / Shift+Tab | next / previous field |
| Alt + underlined letter | jump to that field |
| Ctrl+Enter, Alt+D | done: the rule goes into the list (editor) |
| Alt+A | save anyway (warnings) |
| Escape, Alt+B | back to the list |

## Architecture

| Module | Responsibility |
|---|---|
| `rules.py`, `validate.py`, `safety.py` | qvm-firewall-ui's rule model, engine validation and lockout analysis, **copied unchanged** (v1.0.0); their tests come along |
| `model.py` | rows, the editor's fields, the working copy, the save check |
| `backend/base.py` | `Backend` interface and the guarded save: stale check, snapshot, write |
| `backend/qubesadmin_backend.py` | The real thing: `vm.firewall` |
| `backend/mock.py` | Demo data for qubes-dashboard's demo qubes |
| `window.py` | `FirewallWindow`; the only module, with `frontend.py`, importing `gi` |
| `frontend.py` | `qubes-firewall <qube>` |
| `cli.py` | Headless listing, for checking the backend in dom0 without a display |
| `firewall.css` | qubes-settings' chrome, plus the list and entries |

`FirewallWindow` takes the same arguments as qubes-settings' `SettingsWindow`
(`backend, name, parent=, on_close=, alpha=, theme=`), so qubes-dashboard opens
both the same way. The core (model, backend, cli) does not import `gi`, and
imports `qubesadmin` only in the qubesadmin backend, so it runs and is tested on
any Linux box.

Compared with qvm-firewall-ui, this is the one-qube window, not the fleet
manager: no qube list, no saved profiles, no dry-run CLI. The rules, their
validation and the warnings are the same code.

## Running

```console
$ bin/qubes-firewall work                 # from a checkout
$ bin/qubes-firewall --mock browser-untrusted
$ python3 -m qubes_firewall.cli work      # headless: the list and its warnings
$ python3 -m qubes_firewall.cli --raw work
```

Anywhere but dom0 (no `qubesadmin`), `--backend auto` falls back to demo data.

In qubes-dashboard, put this checkout next to it (`~/.local/share/qubes-firewall`
beside `~/.local/share/qubes-dashboard`); its `bin/qubes-dashboard` puts it on
`PYTHONPATH`, and its `packaging/install-on-dom0.sh` installs all three repos.

Tests use the standard library only; the window tests are skipped without GTK 3
and a display (`xvfb-run sh run-tests.sh` runs them headless):

```console
$ ./run-tests.sh
```
