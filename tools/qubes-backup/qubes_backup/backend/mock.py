"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-dashboard's demo set, with the same power states. A
backup or restore takes simulated time and writes the kind of output the
stock tools do; the passphrase ``wrong`` makes a restore fail as a wrong one
would. Nothing touches a real system.
"""

from __future__ import annotations

import threading
import time

from .. import model
from ..model import BackupRequest, Output, QubeInfo, RestoreRequest, Revision
from .base import Backend, BackendError, Cancelled, Job, Report

RUNNING, HALTED, PAUSED = model.RUNNING, model.HALTED, "Paused"

#: (name, class, state): qubes-dashboard's demo qubes.
_QUBES = (
    ("dom0", "AdminVM", RUNNING),
    ("ai-llm", "AppVM", RUNNING),
    ("browser-personal", "AppVM", RUNNING),
    ("browser-social", "AppVM", RUNNING),
    ("browser-streaming", "AppVM", PAUSED),
    ("browser-untrusted", "AppVM", HALTED),
    ("disp1234", "DispVM", RUNNING),
    ("debian-13", "TemplateVM", HALTED),
    ("debian-13-gnome-browser", "TemplateVM", HALTED),
    ("debian-13-gnome-devel", "TemplateVM", HALTED),
    ("debian-13-gnome-office", "TemplateVM", HALTED),
    ("debian-13-gnome-personal", "TemplateVM", HALTED),
    ("debian-13-gnome-sys", "TemplateVM", HALTED),
    ("default-dvm", "AppVM", HALTED),
    ("development", "AppVM", HALTED),
    ("games", "StandaloneVM", HALTED),
    ("media", "AppVM", HALTED),
    ("office", "AppVM", HALTED),
    ("personal", "AppVM", RUNNING),
    ("sys-firewall", "DispVM", RUNNING),
    ("sys-net", "AppVM", RUNNING),
    ("sys-usb", "DispVM", RUNNING),
    ("sys-vpn", "AppVM", RUNNING),
    ("sys-vpn-secure", "AppVM", RUNNING),
    ("vault", "AppVM", HALTED),
    ("work", "AppVM", RUNNING),
)

#: Hours ago each demo qube's private revisions were taken, oldest first, as
#: an LVM pool with revisions_to_keep 1 or 2 would have them. games has none.
_REVISION_AGES = {"vault": (52, 3), "development": (26,), "office": (75, 30),
                  "media": (2,), "browser-untrusted": (8,), "default-dvm": (200,)}

#: Backups already on the demo USB stick.
_BACKUP_DIR = "/media/usb/backups"
_BACKUPS = ("qubes-backup-2026-10-12T091544", "qubes-backup-2026-10-19T201103")

#: The passphrase a demo restore rejects.
WRONG_PASSPHRASE = "wrong"


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales the simulated time a backup
    or restore takes (0 in tests)."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self._lock = threading.RLock()
        self._qubes = {name: QubeInfo(name, klass, state) for name, klass, state in _QUBES}
        now = time.time()
        self._revisions = {
            name: [Revision(f"{int(now - h * 3600)}-back", float(int(now - h * 3600)))
                   for h in ages]
            for name, ages in _REVISION_AGES.items()
        }
        self._files = {("sys-usb", _BACKUP_DIR): list(_BACKUPS)}
        #: What was run, newest last (for tests).
        self.backups: list[BackupRequest] = []
        self.restores: list[RestoreRequest] = []
        self.reverts: list[tuple[str, str]] = []

    def set_state(self, name: str, state: str) -> None:
        with self._lock:
            info = self._qubes[name]
            self._qubes[name] = QubeInfo(name, info.klass, state)

    def set_states(self, states: dict[str, str]) -> None:
        """Take the power states of qubes-dashboard's own demo backend, so
        that the two simulated systems agree on which qubes are off."""
        with self._lock:
            for name, state in states.items():
                if name in self._qubes:
                    self.set_state(name, state)

    def qube_info(self, name: str) -> QubeInfo:
        with self._lock:
            try:
                return self._qubes[name]
            except KeyError as exc:
                raise BackendError(f"No such qube: {name}") from exc

    def storage_qubes(self) -> list[str]:
        with self._lock:
            names = sorted(q.name for q in self._qubes.values()
                           if q.klass not in ("AdminVM", "TemplateVM"))
        return [model.ADMIN_NAME, *names]

    def revisions(self, name: str) -> list[Revision]:
        with self._lock:
            return list(reversed(self._revisions.get(name, [])))

    def list_backups(self, qube: str, directory: str, start: bool = False) -> list[str]:
        info = self.qube_info(qube)
        if info.state != RUNNING and not start:
            raise BackendError(f"{qube} is not running")
        with self._lock:
            if (qube, directory.rstrip("/") or "/") not in self._files:
                raise BackendError(f"ls: cannot access '{directory}': No such file or directory")
            return model.backup_files(self._files[(qube, directory.rstrip("/") or "/")])

    # -- the long ones ---------------------------------------------------
    def _sleep(self, job: Job, seconds: float) -> None:
        deadline = time.monotonic() + seconds * self.delay
        while time.monotonic() < deadline:
            if job.cancelled:
                break
            time.sleep(min(0.05, deadline - time.monotonic()))
        if job.cancelled:
            raise Cancelled("Cancelled")

    def backup(self, request: BackupRequest, job: Job, report: Report) -> None:
        if request.dest_qube not in self.storage_qubes():
            raise BackendError(f"qvm-backup failed: no such domain: '{request.dest_qube}'")
        with self._lock:
            self.backups.append(request)
        report(Output(line="------------------+--------------+--------------+"))
        report(Output(line="               VM |         type |         size |"))
        report(Output(line=f"{request.qube:>17} | {'VM':>12} | {'2.1 GiB':>12} |"))
        report(Output(line="------------------+--------------+--------------+"))
        where = request.directory
        if request.dest_qube != model.ADMIN_NAME:
            where = f"{request.dest_qube}:{where}"
        report(Output(line=f"VMs not selected for backup: everything but {request.qube}"))
        report(Output(line=f"Backup location: {where}"))
        for percent in range(0, 101, 5):
            self._sleep(job, 0.08)
            report(Output(percent=float(percent)))
        name = time.strftime("qubes-backup-%Y-%m-%dT%H%M%S")
        with self._lock:
            self._files.setdefault(
                (request.dest_qube, request.directory.rstrip("/") or "/"), []).append(name)
        report(Output(line=f"Backup written: {where.rstrip('/')}/{name}"))

    def restore(self, request: RestoreRequest, job: Job, report: Report) -> None:
        with self._lock:
            self.restores.append(request)
        report(Output(line="Checking backup content..."))
        self._sleep(job, 0.5)
        directory, name = model.split_path(request.path)
        with self._lock:
            known = name in self._files.get((request.source_qube, directory), [])
        if not known:
            raise BackendError(f"qvm-backup-restore failed: {request.path}: "
                               "No such file or directory")
        if request.passphrase == WRONG_PASSPHRASE:
            raise BackendError("qvm-backup-restore failed: Invalid backup header "
                               "(wrong passphrase?)")
        new_name = f"{request.qube}1"
        report(Output(line="The following VMs are included in the backup:"))
        report(Output(line=f"{request.qube:>17} | AppVM | {new_name} <-- renamed"))
        if request.verify_only:
            report(Output(line="The above VM archive(s) will be verified."))
            report(Output(line="Existing VMs will NOT be removed or altered."))
        else:
            report(Output(line="The above VMs will be copied and added to your system."))
            report(Output(line="Existing VMs will NOT be removed."))
        report(Output(line="Extracting data: 2.1 GiB to restore"))
        self._sleep(job, 2.0)
        verb = "Verifying" if request.verify_only else "Restoring"
        report(Output(line=f"-> {verb} {new_name}..."))
        self._sleep(job, 0.5)
        if not request.verify_only:
            with self._lock:
                self._qubes[new_name] = QubeInfo(new_name, "AppVM", HALTED)
        report(Output(line="-> Done."))

    def revert(self, name: str, revision: str) -> None:
        info = self.qube_info(name)
        if not info.is_shut_down:
            raise BackendError(f"Domain {name} is not halted")
        with self._lock:
            revisions = self._revisions.get(name, [])
            if revision not in [r.id for r in revisions]:
                raise BackendError(f"No such revision: {revision}")
            # The revision becomes the volume; it is no longer a revision.
            self._revisions[name] = [r for r in revisions if r.id != revision]
            self.reverts.append((name, revision))
