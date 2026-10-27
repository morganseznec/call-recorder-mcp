"""Recording folders: one folder per recording, small JSON files for state.

A folder holds:
  mic.wav           your microphone
  remote.wav        what the apps play (the other side)
  audio.wav         an imported file (instead of mic/remote)
  conversation.wav  both sides mixed, to listen to
  capture.json      capture state, written by CallRecorder.app
  meta.json         title, source, language, number of speakers
  transcription.json  transcription job state
  transcript.md     the transcript (transcript.json for the turns)
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .i18n import t


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write, so a reader never sees half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def read_meta(folder: Path) -> dict[str, Any]:
    return read_json(folder / "meta.json")


def update_meta(folder: Path, **fields: Any) -> dict[str, Any]:
    meta = {**read_meta(folder), **fields}
    write_json(folder / "meta.json", meta)
    return meta


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text)[:50]


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def create(label: str, **meta: Any) -> Path:
    """New folder named <date>_<time>_<label>, with its meta.json."""
    started = datetime.now()
    folder = config.RECORDINGS / f"{started:%Y-%m-%d_%H%M%S}_{slug(label) or 'enregistrement'}"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "meta.json", {**meta, "started_at": started.isoformat(timespec="seconds")})
    return folder


def recent(limit: int | None = None) -> list[Path]:
    """Recording folders, newest first."""
    if not config.RECORDINGS.is_dir():
        return []
    folders = sorted(
        (p for p in config.RECORDINGS.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name,
        reverse=True,
    )
    return folders[:limit]


def resolve(reference: str) -> Path:
    """'latest', a folder name or a path -> recording folder (ValueError if missing)."""
    if reference in ("", "latest"):
        folders = recent(1)
        if not folders:
            raise ValueError(t("no_recordings", folder=config.RECORDINGS))
        return folders[0]
    path = Path(reference).expanduser()
    if not path.is_absolute():
        path = config.RECORDINGS / reference
    if not path.is_dir():
        raise ValueError(t("recording_not_found", reference=reference))
    return path
