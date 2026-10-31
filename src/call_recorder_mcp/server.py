"""MCP server: the tools Claude calls.

Tool descriptions are in English for the model; replies and transcripts are in
the user's language (English or French, see i18n.py).

Tools are synchronous: MCP SDK v2 runs them on worker threads, so waiting for a
capture or a transcription never blocks the server.
"""

import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, audio, capture, config, jobs, storage, transcription
from . import live as live_feed  # `live` is a start_recording parameter
from .i18n import language as default_language
from .i18n import t

INSTRUCTIONS = (
    "Records an audio conversation on this Mac (WhatsApp, Teams or Zoom call, meeting in the browser...) "
    "and transcribes it locally with Whisper. Flow: start_recording, then stop_recording when the user "
    "says the call is over (it returns the transcript). The transcript labels the user's microphone "
    'as "Me" ("Moi" in French) and what the apps play as the other side. For a group call, pass the '
    "number of people on the other side (speakers) to separate their voices; ask the user if you do not "
    "know it. Then rename_speakers replaces the numbered labels with real names. To help the user during "
    "the call (follow-up questions, vague answers), start with live=true and follow get_live_transcript. "
    "Speak to the user in their own language."
)

mcp = MCPServer("call-recorder", version=__version__, instructions=INSTRUCTIONS)


def hints(*, read_only: bool, destructive: bool = False, idempotent: bool = True) -> ToolAnnotations:
    """All four MCP hints, always explicit (some tool directories reject a missing one).

    openWorldHint is always false: capture, files and transcription stay on this Mac.
    """
    return ToolAnnotations(
        read_only_hint=read_only, destructive_hint=destructive, idempotent_hint=idempotent, open_world_hint=False
    )


READ_ONLY = hints(read_only=True)


def _transcript(folder: Path, wait_seconds: float) -> str:
    """The transcript, or where the job stands after waiting up to wait_seconds."""
    state, error = jobs.wait(folder, wait_seconds)
    if state is jobs.Status.DONE:
        return f"{(folder / 'transcript.md').read_text()}\n{t('files', folder=folder)}"
    if state is jobs.Status.ERROR:
        return t("failed", error=error, folder=folder)
    if state is jobs.Status.CRASHED:
        return t("crashed", log=folder / "transcription.log")
    if state is jobs.Status.NONE:
        return t("not_transcribed", name=folder.name)
    return t("in_progress", name=folder.name)


def _transcribe(folder: Path, language: str, speakers: int, wait_seconds: float) -> str:
    meta = storage.update_meta(folder, speakers=speakers) if speakers > 0 else storage.read_meta(folder)
    jobs.start(folder, language or meta.get("language") or default_language())
    return _transcript(folder, wait_seconds)


@mcp.tool(annotations=hints(read_only=False, idempotent=False))
def start_recording(
    source: str = "system",
    title: str = "",
    include_mic: bool = True,
    language: str = "",
    speakers: int = 1,
    live: bool = False,
) -> str:
    """Start recording a call or a conversation.

    source: "system" (all the Mac's audio, the safest), "whatsapp", "teams", "zoom", "slack",
      "facetime", "browser" (all browsers), "chrome", "safari", "firefox", "arc", "edge",
      or a bundle ID prefix (e.g. "com.microsoft.teams2").
    title: name of the conversation (used for the folder name).
    include_mic: also record the microphone (the user's voice).
    language: transcription language ("en", "fr", "auto"...). Defaults to the user's language.
    speakers: number of people on the other side (default 1). Above 1, their voices are
      separated in the transcript (Speaker 1, Speaker 2...).
    live: also transcribe as the call goes, to follow it with get_live_transcript. Use it when
      the user wants help during the call (questions to ask, answers to dig into).
    """
    if not capture.is_installed():
        return t("not_installed")
    if current := capture.active():
        return t("already_recording", name=current.name)
    try:
        key, bundles = capture.resolve_source(source)
    except ValueError as error:
        return str(error)

    folder = storage.create(
        title or key,
        title=title or t("default_title", source=key),
        source=key,
        bundles=bundles,
        language=language or default_language(),
        speakers=max(1, speakers),
    )
    state = capture.start(folder, bundles, include_mic)
    if state.get("state") == "error":
        return t("start_failed", error=state.get("error"))
    if state.get("state") != "recording":
        return t("waiting_permission", name=folder.name, state=state.get("state", "?"))
    notes = []
    if include_mic and not state.get("mic"):
        notes.append(t("mic_missing"))
    if bundles:
        notes.append(t("app_only", source=key))
    if live:
        live_feed.start(folder, storage.read_meta(folder)["language"])
        notes.append(t("live_on"))
    return t("recording", name=folder.name) + (t("notes", notes="; ".join(notes)) if notes else "")


@mcp.tool(annotations=READ_ONLY)
def recording_status() -> str:
    """Whether a recording is in progress, for how long, and the recent sound levels of each side
    (-120 dB on the other side means no sound is being captured)."""
    folder = capture.active()
    if not folder:
        return t("no_recording")
    state = capture.state(folder)
    levels = [
        f"{t(label)} {state[key]:.0f} dB"
        for key, label in (("remote_peak_db", "level_remote"), ("mic_peak_db", "level_mic"))
        if key in state
    ]
    lines = [
        t("status_name", name=folder.name),
        t("status_duration", duration=storage.format_duration(state.get("elapsed_seconds", 0))),
        t("status_levels", levels=", ".join(levels) or "n/a"),
    ]
    if "remote_sources" in state:
        lines.append(t("status_apps", apps=", ".join(state["remote_sources"]) or t("status_no_apps")))
    if state.get("mic_device"):
        lines.append(t("status_mic", device=state["mic_device"]))
    return "\n".join(lines)


@mcp.tool(annotations=hints(read_only=False))
def stop_recording(transcribe: bool = True, language: str = "", speakers: int = 0, wait_seconds: int = 30) -> str:
    """Stop the recording in progress and start its transcription.

    speakers: number of people on the other side, if different from the one given at start
      (0 = unchanged). For a group call, ask the user.
    language: overrides the language chosen at start.
    Returns the transcript if it is ready within wait_seconds, otherwise says to call
    get_transcript later.
    """
    folder = capture.active()
    if not folder:
        return t("no_recording")
    started = capture.state(folder).get("state") == "recording"
    duration = storage.format_duration(capture.stop(folder).get("duration_seconds", 0))
    if not started:
        return t("cancelled")
    if not transcribe:
        return t("stopped_at", duration=duration, folder=folder)
    return f"{t('stopped', duration=duration)}\n\n{_transcribe(folder, language, speakers, wait_seconds)}"


@mcp.tool(annotations=READ_ONLY)
def get_live_transcript(since: int = 0, wait_seconds: int = 20) -> str:
    """Live transcript of the recording in progress (started with live=true): what was said since
    the cursor `since`, about a second after each sentence ends. Waits up to wait_seconds for new
    speech. Rough text: the final transcript after the call is more accurate.

    To follow a call and help the user in real time, call it in a loop within the same turn,
    passing the returned cursor as `since`. After each batch, tell the user in a few lines (they
    are on the call, keep it short): which of their planned questions got answered, where the
    other side stayed vague, evasive or unconvincing, and one or two precise follow-up questions
    to ask now. Say nothing when a batch brings nothing useful. Stop when the recording is over.
    """
    folder = capture.active()
    if not folder:
        try:
            folder = storage.resolve("latest")
        except ValueError:
            return t("no_recording")
    if not live_feed.status(folder):
        return t("live_off")
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        lines = live_feed.read_lines(folder)
        state = live_feed.status(folder)
        finished = state.get("state") in ("ended", "error")
        if len(lines) > since or finished or time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    new = sorted(lines[since:], key=lambda line: line["start"])
    output = [
        t("turn", time=storage.format_duration(line["start"]), speaker=f" {line['speaker']}", text=line["text"])
        for line in new
    ]
    if not new and not finished:
        output.append(t("live_nothing_new"))
    if state.get("state") == "error":
        output.append(t("live_failed", error=state.get("error", "")))
    elif finished:
        output.append(t("live_ended"))
    else:
        output.append(t("live_next", cursor=len(lines)))
    return "\n".join(output)


@mcp.tool(annotations=READ_ONLY)
def get_transcript(recording: str = "latest", wait_seconds: int = 30) -> str:
    """Transcript of a recording ("latest" or the folder name), waiting up to wait_seconds
    if it is still running."""
    try:
        folder = storage.resolve(recording)
    except ValueError as error:
        return str(error)
    if folder == capture.active():
        return t("still_recording")
    return _transcript(folder, wait_seconds)


@mcp.tool(annotations=hints(read_only=False, destructive=True))
def transcribe_recording(
    recording: str = "latest", language: str = "", speakers: int = 0, wait_seconds: int = 30
) -> str:
    """Transcribe an existing recording again, e.g. in another language, or to separate voices:
    speakers = number of people on the other side (0 = unchanged)."""
    try:
        folder = storage.resolve(recording)
    except ValueError as error:
        return str(error)
    if folder == capture.active():
        return t("still_recording")
    return _transcribe(folder, language, speakers, wait_seconds)


@mcp.tool(annotations=hints(read_only=False, idempotent=False))
def transcribe_file(path: str, title: str = "", language: str = "", speakers: int = 1, wait_seconds: int = 30) -> str:
    """Transcribe an existing audio or video file (WhatsApp voice note .opus, .m4a, .mp3, .mp4,
    meeting recording...), copied into a new recording folder.

    speakers: total number of people speaking in the file (above 1, their voices are separated
      into Speaker 1, Speaker 2...).
    """
    source = Path(path).expanduser()
    if not source.is_file():
        return t("file_not_found", path=path)
    folder = storage.create(
        title or source.stem,
        title=title or source.name,
        source=str(source),
        language=language or default_language(),
        speakers=max(1, speakers),
    )
    try:
        audio.import_file(source, folder / "audio.wav")
    except ValueError as error:
        return str(error)
    return _transcribe(folder, language, 0, wait_seconds)


@mcp.tool(annotations=hints(read_only=False, destructive=True))
def rename_speakers(names: dict[str, str], recording: str = "latest") -> str:
    """Replace speaker labels in a transcript with real names, e.g. {"Speaker 1": "Alice"}
    (labels are in the user's language, as shown in the transcript). Returns the updated transcript."""
    try:
        folder = storage.resolve(recording)
        transcription.rename_speakers(folder, names)
    except ValueError as error:
        return str(error)
    return _transcript(folder, 0)


@mcp.tool(annotations=READ_ONLY)
def list_recordings(limit: int = 10) -> str:
    """Latest recordings and their transcription state."""
    folders = storage.recent(limit)
    if not folders:
        return t("no_recordings", folder=config.RECORDINGS)
    current = capture.active()
    lines = [t("folder", folder=config.RECORDINGS)]
    for folder in folders:
        duration = capture.state(folder).get("duration_seconds")
        state = t("state_recording") if folder == current else t(f"state_{jobs.status(folder)[0].value}")
        lines.append(
            f"- {folder.name} | {storage.read_meta(folder).get('title', '')} | "
            f"{storage.format_duration(duration) if duration else '-'} | {state}"
        )
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
def list_audio_sources() -> str:
    """Apps currently using audio (to choose the source), and the default microphone and output."""
    if not capture.is_installed():
        return t("not_installed")
    data = capture.audio_sources()
    shown_apple = ("com.apple.Safari", "com.apple.WebKit.GPU", "com.apple.FaceTime", "com.apple.avconferenced")
    apps = sorted(
        {
            (p["bundle_id"], p["name"], p["playing"])
            for p in data["processes"]
            if p["bundle_id"] and (not p["bundle_id"].startswith("com.apple.") or p["bundle_id"] in shown_apple)
        }
    )
    lines = [
        t("sources_mic", device=data["default_input"]),
        t("sources_output", device=data["default_output"]),
        t("sources_apps"),
    ]
    lines += [f"- {bundle} ({name or '?'}){t('sources_playing') if playing else ''}" for bundle, name, playing in apps]
    lines.append(t("sources_shortcuts", sources=", ".join(capture.SOURCES)))
    return "\n".join(lines)


def main() -> None:
    config.extend_path()
    config.RECORDINGS.mkdir(parents=True, exist_ok=True)
    mcp.run()
