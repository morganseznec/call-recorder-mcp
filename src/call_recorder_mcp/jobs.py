"""Background transcription jobs.

A long call takes minutes to transcribe, longer than an MCP client waits for a
tool call. The transcription therefore runs in a detached process
(``python -m call_recorder_mcp.jobs FOLDER LANGUAGE``) that records its state in
``transcription.json``; tools wait for it for a while, then let the client poll.
"""

import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from enum import Enum
from pathlib import Path

from . import config, storage, transcription
from .capture import process_alive

STATUS_FILE = "transcription.json"


class Status(str, Enum):
    NONE = "none"  # never transcribed
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CRASHED = "crashed"  # the worker died without reporting


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start(folder: Path, language: str) -> None:
    storage.write_json(folder / STATUS_FILE, {"state": Status.QUEUED.value, "language": language})
    with open(folder / "transcription.log", "w") as log:
        subprocess.Popen(
            [sys.executable, "-m", "call_recorder_mcp.jobs", str(folder), language],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


def status(folder: Path) -> tuple[Status, str]:
    """Job status and, for an error, its message."""
    data = storage.read_json(folder / STATUS_FILE)
    if not data:
        return Status.NONE, ""
    state = Status(data.get("state", Status.NONE.value))
    if state is Status.RUNNING and not process_alive(data.get("pid")):
        return Status.CRASHED, ""
    return state, data.get("error", "")


def wait(folder: Path, timeout: float) -> tuple[Status, str]:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        state, error = status(folder)
        if state not in (Status.QUEUED, Status.RUNNING) or time.monotonic() >= deadline:
            return state, error
        time.sleep(1)


def run(folder: Path, language: str) -> None:
    path = folder / STATUS_FILE
    storage.write_json(path, {"state": Status.RUNNING.value, "pid": os.getpid(), "started_at": _now()})
    try:
        transcription.transcribe(folder, language)
    except Exception as error:
        detail = getattr(error, "stderr", "") or ""
        storage.write_json(path, {"state": Status.ERROR.value, "error": f"{error} {detail}".strip()[-2000:]})
        traceback.print_exc()
        raise SystemExit(1) from error
    storage.write_json(path, {"state": Status.DONE.value, "finished_at": _now()})


if __name__ == "__main__":
    config.extend_path()
    run(Path(sys.argv[1]), sys.argv[2])
