"""Running the qvm-* tools: the passphrase on stdin, output as it comes.

Kept apart from qubesadmin so that it is tested on any box, with a stand-in
command.
"""

from __future__ import annotations

import codecs
import os
import signal
import subprocess

from ..model import OutputParser
from .base import BackendError, Cancelled, Job, Report

#: How many of the last log lines an error message quotes.
ERROR_LINES = 3
#: The most a qube's listing of a directory may be; it is untrusted.
MAX_LISTING = 1024 * 1024
LISTING_TIMEOUT = 60


def run_tool(argv: list[str], stdin: bytes, job: Job, report: Report) -> None:
    """Run ``argv``, feed it ``stdin``, report its output (stdout and stderr
    together) line by line. Cancelling the job sends SIGINT, which qvm-backup
    turns into a cancel of the backup in qubesd."""
    try:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # Its own session: a SIGINT for the list's terminal is not for it.
            start_new_session=True,
        )
    except OSError as exc:
        raise BackendError(f"Cannot run {argv[0]}: {exc.strerror or exc}") from exc

    def interrupt():
        if process.poll() is None:
            try:
                os.kill(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    job.on_cancel(interrupt)
    parser = OutputParser()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    lines: list[str] = []

    def deliver(outputs):
        for output in outputs:
            if output.line is not None:
                lines.append(output.line)
                del lines[:-ERROR_LINES]
            report(output)

    try:
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except BrokenPipeError:
            pass  # It exited before reading; its output says why.
        while True:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            deliver(parser.feed(decoder.decode(chunk)))
        deliver(parser.feed(decoder.decode(b"", final=True)))
        deliver(parser.close())
        code = process.wait()
    finally:
        job.on_cancel(None)
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()

    if job.cancelled:
        raise Cancelled("Cancelled")
    if code != 0:
        detail = " ".join(lines) or f"exit status {code}"
        raise BackendError(f"{argv[0]} failed: {detail}")


def run_quiet(argv: list[str], timeout: float = LISTING_TIMEOUT) -> str:
    """Run ``argv`` and return its output (at most MAX_LISTING bytes);
    BackendError with its error output when it fails."""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
    except OSError as exc:
        raise BackendError(f"Cannot run {argv[0]}: {exc.strerror or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"{argv[0]} did not answer in {timeout:.0f} seconds") from exc
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        last = error.splitlines()[-1] if error else f"exit status {result.returncode}"
        raise BackendError(last)
    return result.stdout[:MAX_LISTING].decode("utf-8", errors="replace")
