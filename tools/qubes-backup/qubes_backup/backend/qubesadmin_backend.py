"""The real thing, in dom0: qubesadmin for what the qubes are, and the stock
tools for the work -- ``qvm-backup``, ``qvm-backup-restore`` and
``qvm-volume`` -- so that a backup made here is the same as one made by hand.
"""

from __future__ import annotations

import os

import qubesadmin
import qubesadmin.exc

from .. import model
from ..model import BackupRequest, QubeInfo, RestoreRequest, Revision
from .base import Backend, BackendError, Job, Report
from .tools import run_quiet, run_tool

QVM_RUN = "qvm-run"


class QubesAdminBackend(Backend):

    name = "qubesadmin"

    @staticmethod
    def _app():
        # A fresh Qubes object per call keeps worker threads independent.
        return qubesadmin.Qubes()

    def qube_info(self, name: str) -> QubeInfo:
        try:
            vm = self._app().domains[name]
            return QubeInfo(name, vm.klass, vm.get_power_state())
        except KeyError as exc:
            raise BackendError(f"No such qube: {name}") from exc
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(str(exc) or type(exc).__name__) from exc

    def storage_qubes(self) -> list[str]:
        try:
            names = sorted(vm.name for vm in self._app().domains
                           if vm.klass not in ("AdminVM", "TemplateVM"))
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(str(exc) or type(exc).__name__) from exc
        return [model.ADMIN_NAME, *names]

    def revisions(self, name: str) -> list[Revision]:
        return model.parse_revisions(run_quiet(model.revisions_command(name)))

    def list_backups(self, qube: str, directory: str, start: bool = False) -> list[str]:
        if qube == model.ADMIN_NAME:
            try:
                return model.backup_files(os.listdir(directory))
            except OSError as exc:
                raise BackendError(f"{directory}: {exc.strerror or exc}") from exc
        if not model.is_qube_name(qube):
            raise BackendError(f"“{qube}” is not a qube name")
        if not start and not self._is_running(qube):
            raise BackendError(f"{qube} is not running")
        argv = [QVM_RUN, "--pass-io", "--no-gui", "--quiet"]
        if not start:
            argv.append("--no-autostart")
        # No "--", as in model.backup_command: the name starts with a letter.
        argv += [qube, model.list_backups_script(directory)]
        return model.backup_files(run_quiet(argv).splitlines())

    def _is_running(self, name: str) -> bool:
        try:
            return self._app().domains[name].is_running()
        except (KeyError, qubesadmin.exc.QubesException):
            return False

    def backup(self, request: BackupRequest, job: Job, report: Report) -> None:
        run_tool(model.backup_command(request), model.stdin_for(request), job, report)

    def restore(self, request: RestoreRequest, job: Job, report: Report) -> None:
        run_tool(model.restore_command(request), model.stdin_for(request), job, report)

    def revert(self, name: str, revision: str) -> None:
        run_quiet(model.revert_command(name, revision))
