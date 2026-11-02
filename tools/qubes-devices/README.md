# qubes-devices

A PCI devices window for one qube, for Qubes OS 4.3: qube Settings' Devices
tab as one keyboard-first list, in qubes-firewall's window. It is the
**Devices** action in [qubes-dashboard](../..), and it also runs
on its own:

```console
$ qubes-devices games
```

## What it shows

Every PCI device dom0 has (PCI bridges left out), one row each, with a check
box for the devices **assigned** to the qube. An assigned device is attached
whenever the qube starts (`required` assignment), as with qube Settings'
*Devices always connected to this qube*.

| Column | |
|---|---|
| ✓ | assigned to this qube |
| Address | `00:01.0 -> 00:00.0`: function 00.0 on the bus behind root port 00:01.0 (qubesd's port `00_01.0-00_00.0`) |
| Class | Display, Audio, Network, PCI USB, … |
| Device | vendor and product |
| Reset | for assigned devices: *strict*, or *not strict* when strict reset is off (`no-strict-reset`) |
| Used by | other qubes the device is assigned to, *(running)* when it is attached to them now |

A device that is assigned but that dom0 does not have (any more) is listed as
*not found in dom0*, so it can still be unassigned.

## Only while shut down

Assignments can only be changed while the qube is **halted**: qubesd attaches
PCI devices when a qube starts. For a qube that is on, the window shows its
devices with a note and only a Close button. dom0 is read-only too.

Save checks again: if the qube was started meanwhile, nothing is written.

## Editing

Nothing reaches the qube before **Save**. Space (or the check box, or
**Assign** / **Unassign**) changes the window's working copy; the subtitle
says *not saved* while it differs.

**Turn strict reset off** (Alt+R) sets `no-strict-reset` on an assigned device:
for a device without a working reset (many GPUs and USB controllers), which Xen
otherwise refuses to give to a qube. It is a trade-off: what one qube leaves in
the device may reach the next qube that gets it.

**Save** shows these warnings first and asks again (**Save anyway** / **Back**,
focus on Back):

- the qube uses **PVH**, which cannot have PCI devices (it will not start);
- the qube is in **memory balancing** (a qube with PCI devices should have
  fixed memory);
- a newly assigned device is **assigned to another qube** (only one of them can
  run at a time), or attached to a running one now;
- **strict reset** was turned off for a device;
- a card behind a root port is **only partly assigned**, like a GPU without its
  HDMI audio. Devices built into the chipset (no `->` in the address) share
  slots without belonging together, and are not checked.

Then it writes, like qubes-firewall's guarded write path:

1. re-reads the qube's power state and assignments, and stops, writing nothing,
   if it is no longer halted or another tool changed them;
2. saves the current assignments to
   `~/.local/state/qubes-devices/<qube>.<UTC time>.pci`, as the
   `qvm-pci assign --required …` commands that recreate them;
3. unassigns removed devices, unassigns and assigns again those whose strict
   reset changed (qubesd cannot change an assignment's options in place), and
   assigns new ones -- `admin.vm.device.pci.Unassign` / `Assign`, through
   qubesadmin's own `DeviceAssignment`. A new assignment is bound to the
   device's identity at that port, as `qvm-pci assign` does by default.

If qubesd refuses a step, the error says so and names the snapshot file, and
the window stays open with your edits.

## Keys

| key | in the list |
|---|---|
| Up / Down | select a device |
| Space, Enter | assign / unassign |
| Alt+R | turn strict reset off / on |
| *typing* | filter (address, class, device, used by); Up/Down go back to the list |
| Alt+O | assigned only |
| Ctrl+Enter, Ctrl+S | save and close |
| Escape | clear the filter; then close without saving |

| key | in the warnings |
|---|---|
| Alt+A, Ctrl+Enter | save anyway |
| Escape, Alt+B | back to the list |

## Architecture

| Module | Responsibility |
|---|---|
| `model.py` | devices, assignments, rows, the filter, the working copy, the save check |
| `backend/base.py` | `Backend` interface and the guarded save: state and stale check, snapshot, write |
| `backend/qubesadmin_backend.py` | The real thing: `dom0.devices["pci"]`, `vm.devices["pci"]` (Qubes 4.3 device API) |
| `backend/mock.py` | Demo data for qubes-dashboard's demo qubes |
| `window.py` | `DevicesWindow`; the only module, with `frontend.py`, importing `gi` |
| `frontend.py` | `qubes-devices <qube>` |
| `cli.py` | Headless listing, for checking the backend in dom0 without a display |
| `devices.css` | qubes-firewall's chrome, for the device list |

`DevicesWindow` takes the same arguments as qubes-firewall's `FirewallWindow`
(`backend, name, parent=, on_close=, alpha=, theme=`), so qubes-dashboard opens
both the same way. The core (model, backend, cli) does not import `gi`, and
imports `qubesadmin` only in the qubesadmin backend, so it runs and is tested on
any Linux box.

To say which other qubes have a device, the window reads every qube's
assignments: one `admin.vm.device.pci.Assigned` call per qube, in the
background while the window loads. Qubes whose devices the caller may not read
are skipped.

## Running

```console
$ bin/qubes-devices games                 # from a checkout
$ bin/qubes-devices --mock games
$ python3 -m qubes_devices.cli games      # headless: the list and its warnings
$ python3 -m qubes_devices.cli --assigned sys-net
$ python3 -m qubes_devices.cli --raw games   # the qvm-pci commands
```

Anywhere but dom0 (no `qubesadmin`), `--backend auto` falls back to demo data.

In qubes-dashboard, put this checkout next to it (`~/.local/share/qubes-devices`
beside `~/.local/share/qubes-dashboard`); its `bin/qubes-dashboard` puts it on
`PYTHONPATH`, and its `packaging/install-on-dom0.sh` installs it with the other
repos.

Tests use the standard library only; the window tests are skipped without GTK 3
and a display (`xvfb-run sh run-tests.sh` runs them headless):

```console
$ ./run-tests.sh
```
