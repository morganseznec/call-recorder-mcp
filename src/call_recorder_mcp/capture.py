"""Drives CallRecorder.app, the Swift helper that captures the audio.

The app is launched through ``open`` so that macOS treats it as its own
responsible process: the microphone and audio capture permissions are asked
for (and remembered) under "CallRecorder", whatever MCP client started us.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from . import audio, config, storage
from .i18n import t

BROWSERS = [
    "com.google.Chrome",
    "com.apple.Safari",
    "com.apple.WebKit",  # Safari plays audio from its WebKit GPU process
    "org.mozilla.firefox",
    "company.thebrowser",  # Arc
    "com.microsoft.edgemac",
    "com.brave.Browser",
    "com.operasoftware",
    "com.vivaldi",
]

# Source name -> bundle ID prefixes of the processes to capture ([] = whole system).
SOURCES: dict[str, list[str]] = {
    "system": [],
    # WhatsApp calls go through CallKit: the call audio is played by avconferenced.
    "whatsapp": ["net.whatsapp", "desktop.WhatsApp", "com.apple.avconferenced"],
    "teams": ["com.microsoft.teams"],
    "zoom": ["us.zoom"],
    "slack": ["com.tinyspeck.slackmacgap"],
    "facetime": ["com.apple.FaceTime", "com.apple.avconferenced"],
    "browser": BROWSERS,
    "chrome": ["com.google.Chrome"],
    "safari": ["com.apple.Safari", "com.apple.WebKit"],
    "firefox": ["org.mozilla.firefox"],
    "arc": ["company.thebrowser"],
    "edge": ["com.microsoft.edgemac"],
}

START_TIMEOUT = 30  # the first run waits for the user to answer the permission prompts
STOP_TIMEOUT = 20


def _marker() -> Path:
    return config.RECORDINGS / ".active"


def state(folder: Path) -> dict[str, Any]:
    """Capture state written by the app: pid, state, levels, duration..."""
    return storage.read_json(folder / "capture.json")


def is_installed() -> bool:
    return config.APP_BINARY.exists()


def resolve_source(source: str) -> tuple[str, list[str]]:
    """'teams' -> ('teams', [...]); a bundle ID prefix is accepted as is."""
    key = source.strip().lower()
    if key in SOURCES:
        return key, SOURCES[key]
    if "." in key:
        return key, [source.strip()]
    raise ValueError(t("unknown_source", source=source, values=", ".join(SOURCES)))


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def active() -> Path | None:
    """Folder of the recording in progress, if its capture process still runs."""
    try:
        folder = Path(_marker().read_text().strip())
    except OSError:
        return None
    current = state(folder)
    if current.get("state") not in ("stopped", "error") and process_alive(current.get("pid")):
        return folder
    return None


def start(folder: Path, bundles: list[str], include_mic: bool) -> dict[str, Any]:
    """Launches the capture and waits until it records (or needs a permission)."""
    command = ["open", "-n", "-g", str(config.APP), "--args", "record", "--out", str(folder)]
    if bundles:
        command += ["--bundles", ",".join(bundles)]
    if not include_mic:
        command.append("--no-mic")
    subprocess.run(command, check=True)
    _marker().write_text(str(folder))

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        current = state(folder)
        if current.get("state") in ("recording", "error"):
            return current
        time.sleep(0.5)
    return state(folder)


def stop(folder: Path) -> dict[str, Any]:
    """Stops the capture, then writes conversation.wav (both sides mixed)."""
    pid = state(folder).get("pid")
    if process_alive(pid):
        os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline and state(folder).get("state") != "stopped":
        time.sleep(0.3)
    _marker().unlink(missing_ok=True)
    tracks = [folder / name for name in ("mic.wav", "remote.wav") if (folder / name).exists()]
    audio.mix(tracks, folder / "conversation.wav")
    return state(folder)


def audio_sources() -> dict[str, Any]:
    """Processes known to Core Audio, plus the default input and output devices."""
    output = subprocess.run([str(config.APP_BINARY), "list"], capture_output=True, text=True, check=True).stdout
    return json.loads(output)
