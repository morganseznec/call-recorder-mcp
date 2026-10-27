"""Turns the recorded tracks into a transcript (whisper.cpp, run locally).

Each track is cut into utterances (voice activity detection, or diarization
when several people share the other side), and every utterance is transcribed
as its own short file in a single whisper.cpp run. Short inputs keep exact
timestamps and let the vocabulary prompt apply everywhere: on a whole track,
Whisper's own VAD mode chains utterances across the removed silences once a
prompt is given, which blurs who said what.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from . import audio, config, diarization, storage
from .i18n import language as language_default
from .i18n import t
from .spans import Span, group_utterances

# Recorded tracks; "audio" is an imported file.
TRACKS = ("mic", "remote", "audio")
SILENT_TRACK_DB = -55.0

# Whisper "hears" these on silence or noise (subtitle credits mostly), or echoes its prompt.
HALLUCINATIONS = re.compile(
    r"amara\.org|sous-titr|merci d'avoir regard|abonnez-vous|^\W*(merci|thank you)\W*$|"
    r"radio-canada|transcrit par|thanks? (you )?for watching|subtitles by|^\W*(vocabulaire|glossary)\s*:|"
    r"^\W*\[?(musique|music|rires|silence|applaudissements)\]?\W*$",
    re.IGNORECASE,
)


class Segment(TypedDict):
    start: float
    end: float
    text: str
    speaker: str


def vocabulary_prompt(path: Path | None = None) -> str:
    """Terms of the vocabulary file (one per line, # for comments), as a prompt."""
    try:
        lines = (path or config.VOCABULARY).read_text().splitlines()
    except OSError:
        return ""
    terms = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    return f"{t('glossary')}: {', '.join(terms)}." if terms else ""


def vad_spans(pcm: Path) -> list[Span]:
    """Speech found by the Silero VAD (the tool prints times in 10 ms units)."""
    output = subprocess.run(
        ["whisper-vad-speech-segments", "-vm", str(config.VAD_MODEL), "-f", str(pcm)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Span(float(a) / 100, float(b) / 100) for a, b in re.findall(r"start = ([\d.]+), end = ([\d.]+)", output)]


def utterances(pcm: Path, speakers: int) -> list[Span]:
    if speakers > 1 and diarization.available():
        spans = diarization.speaker_spans(pcm, speakers)
    elif config.VAD_MODEL.exists():
        spans = vad_spans(pcm)
    else:
        spans = [Span(0.0, audio.duration(pcm))]
    return group_utterances(spans)


def run_whisper(files: list[Path], language: str) -> list[list[dict[str, Any]]]:
    """One whisper.cpp run over many files; returns the segments of each file."""
    command = [
        "whisper-cli",
        "-m", str(config.WHISPER_MODEL),
        "-l", language,
        "-t", str(min(8, os.cpu_count() or 4)),
        "-oj", "-np",
    ]  # fmt: skip
    if prompt := vocabulary_prompt():
        command += ["--prompt", prompt]
    for file in files:
        command += ["-f", str(file), "-of", str(file.with_suffix(""))]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return [
        json.loads(file.with_suffix(".json").read_text(errors="replace")).get("transcription", []) for file in files
    ]


def transcribe_track(wav: Path, language: str, workdir: Path, speakers: int = 1) -> list[tuple[Span, str]]:
    """(span in seconds from the start of the track, text) for each Whisper segment."""
    pcm = workdir / f".{wav.stem}.16k.wav"
    chunks_dir = workdir / f".{wav.stem}.chunks"
    try:
        audio.to_pcm16k(wav, pcm)
        spans = utterances(pcm, speakers)
        if not spans:
            return []
        chunks = audio.cut(pcm, [(s.start, s.end) for s in spans], chunks_dir)
        results = run_whisper([path for path, _ in chunks], language)
        segments = []
        for span, (_, offset), items in zip(spans, chunks, results, strict=True):
            for item in items:
                text = item.get("text", "").strip()
                if text and not HALLUCINATIONS.search(text):
                    start = round(offset + item["offsets"]["from"] / 1000, 2)
                    end = round(offset + item["offsets"]["to"] / 1000, 2)
                    segments.append((Span(start, end, span.voice), text))
        return segments
    finally:
        pcm.unlink(missing_ok=True)
        shutil.rmtree(chunks_dir, ignore_errors=True)


def label(track: str, segments: list[tuple[Span, str]], speakers: int) -> list[Segment]:
    """Speaker labels ("Me", "Other side", "Speaker 1"... in English), numbered by first appearance."""
    numbers: dict[int, int] = {}
    labelled = []
    for span, text in sorted(segments, key=lambda item: item[0].start):
        if track == "mic":
            speaker = t("me")
        elif speakers > 1:
            number = numbers.setdefault(span.voice, len(numbers) + 1)
            speaker = t("remote_n" if track == "remote" else "person_n", n=number)
        else:
            speaker = t("remote") if track == "remote" else ""
        labelled.append(Segment(start=span.start, end=span.end, text=text, speaker=speaker))
    return labelled


def drop_bleed(mic: list[Segment], remote: list[Segment]) -> list[Segment]:
    """Without headphones the mic also hears the other side: drop mic segments
    that repeat a remote segment said at the same time."""

    def is_echo(segment: Segment) -> bool:
        return any(
            other["start"] - 2 <= segment["start"] <= other["end"] + 2
            and difflib.SequenceMatcher(None, segment["text"].lower(), other["text"].lower()).ratio() > 0.6
            for other in remote
        )

    return [segment for segment in mic if not is_echo(segment)]


def merge_turns(segments: list[Segment], max_pause: float = 3.0) -> list[Segment]:
    """Joins consecutive segments of the same speaker into one turn."""
    turns: list[Segment] = []
    for segment in sorted(segments, key=lambda s: s["start"]):
        last = turns[-1] if turns else None
        if last and last["speaker"] == segment["speaker"] and segment["start"] - last["end"] < max_pause:
            last["text"] += " " + segment["text"]
            last["end"] = max(last["end"], segment["end"])
        else:
            turns.append(Segment(**segment))
    return turns


def render(folder: Path, turns: list[Segment], language: str) -> None:
    """Writes transcript.md (for people) and transcript.json (the turns)."""
    meta = storage.read_meta(folder)

    def field(key: str, value: str) -> str:
        return t("field", label=t(key), value=value)

    lines = [f"# {meta.get('title') or folder.name}", ""]
    if meta.get("started_at"):
        lines.append(field("date", meta["started_at"].replace("T", " ")))
    if meta.get("source"):
        lines.append(field("source", meta["source"]))
    duration = storage.read_json(folder / "capture.json").get("duration_seconds")
    if duration:
        lines.append(field("duration", storage.format_duration(duration)))
    participants = sorted({turn["speaker"] for turn in turns if turn["speaker"]})
    if len(participants) > 1:
        lines.append(field("participants", ", ".join(participants)))
    lines += [field("language", language), "", f"## {t('transcript')}", ""]

    if not turns:
        lines.append(t("no_speech"))
    for turn in turns:
        speaker = f" {turn['speaker']}" if turn["speaker"] else ""
        lines += [t("turn", time=storage.format_duration(turn["start"]), speaker=speaker, text=turn["text"]), ""]

    (folder / "transcript.md").write_text("\n".join(lines).rstrip() + "\n")
    storage.write_json(folder / "transcript.json", {"language": language, "turns": turns})


def transcribe(folder: Path, language: str) -> None:
    """Transcribes every non-silent track of a recording folder."""
    # People on the other side (or in an imported file); the mic is always one person.
    speakers = max(1, int(storage.read_meta(folder).get("speakers") or 1))
    tracks: dict[str, list[Segment]] = {}
    for track in TRACKS:
        wav = folder / f"{track}.wav"
        if wav.exists() and audio.max_volume_db(wav) > SILENT_TRACK_DB:
            track_speakers = 1 if track == "mic" else speakers
            tracks[track] = label(track, transcribe_track(wav, language, folder, track_speakers), speakers)
    if "mic" in tracks and "remote" in tracks:
        tracks["mic"] = drop_bleed(tracks["mic"], tracks["remote"])
    render(folder, merge_turns([segment for segments in tracks.values() for segment in segments]), language)


def rename_speakers(folder: Path, names: dict[str, str]) -> None:
    """Replaces speaker labels, e.g. {"Speaker 1": "Alice"} (ValueError for unknown labels)."""
    data = storage.read_json(folder / "transcript.json")
    turns: list[Segment] = data.get("turns", [])
    if not turns:
        raise ValueError(t("no_transcript"))
    known = {turn["speaker"] for turn in turns}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(t("unknown_speakers", unknown=", ".join(unknown), known=", ".join(sorted(known))))
    renamed = [Segment(**{**turn, "speaker": names.get(turn["speaker"], turn["speaker"])}) for turn in turns]
    render(folder, merge_turns(renamed), data.get("language") or language_default())
