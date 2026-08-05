from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of a child process monitored with a wall-clock deadline."""

    returncode: int
    elapsed_s: float
    timed_out: bool
    termination_signal: int | None


def _terminate_group(process: subprocess.Popen[bytes], grace_s: float) -> None:
    """Stop the child and all of its descendants, then reap the child."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_supervised(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float | None,
    stdout_path: Path,
    stderr_path: Path,
    terminate_grace_s: float = 2.0,
) -> ProcessResult:
    """Run ``command`` with an optional wall-clock deadline.

    The child starts a new session so a timeout terminates DRSA or other
    descendants as well as the Python worker itself. ``timeout_s=None`` and
    ``timeout_s=0`` disable the deadline.
    """

    if timeout_s is not None and timeout_s < 0:
        raise ValueError("timeout_s must be non-negative or None")
    if terminate_grace_s < 0:
        raise ValueError("terminate_grace_s must be non-negative")

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        timed_out = False
        if timeout_s in (None, 0):
            process.wait()
        else:
            try:
                process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                # Recheck before signalling to avoid classifying a deadline
                # race as a timeout after the child has already exited.
                if process.poll() is None:
                    timed_out = True
                    _terminate_group(process, terminate_grace_s)
        returncode = process.wait()
    elapsed_s = time.monotonic() - start
    return ProcessResult(
        returncode=returncode,
        elapsed_s=elapsed_s,
        timed_out=timed_out,
        termination_signal=-returncode if returncode < 0 else None,
    )
