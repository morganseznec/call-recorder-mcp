"""Audio file helpers (ffmpeg and 16 kHz PCM WAV)."""

import re
import subprocess
import wave
from collections.abc import Iterable
from pathlib import Path

SAMPLE_RATE = 16_000  # what Whisper and the diarization models expect


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)


def to_pcm16k(source: Path, destination: Path) -> None:
    result = _ffmpeg("-i", str(source), "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(destination))
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg: {result.stderr.strip()[-500:]}")


def import_file(source: Path, destination: Path) -> None:
    """Any audio or video file (opus, m4a, mp4...) -> mono 48 kHz WAV."""
    result = _ffmpeg("-i", str(source), "-vn", "-ac", "1", "-ar", "48000", str(destination))
    if result.returncode != 0:
        raise ValueError(f"Impossible de lire ce fichier : {result.stderr.strip()[-500:]}")


def mix(tracks: list[Path], destination: Path) -> None:
    """Mixes the tracks into one file, each side kept at its recorded level."""
    if not tracks:
        return
    inputs = [arg for track in tracks for arg in ("-i", str(track))]
    mixing = ["-filter_complex", f"amix=inputs={len(tracks)}:duration=longest:normalize=0"] if len(tracks) > 1 else []
    _ffmpeg(*inputs, *mixing, "-c:a", "pcm_s16le", str(destination))


def max_volume_db(path: Path) -> float:
    """Peak level in dBFS (-120 for digital silence)."""
    stderr = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    match = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", stderr)
    if not match or match.group(1) == "-inf":
        return -120.0
    return float(match.group(1))


def duration(pcm: Path) -> float:
    with wave.open(str(pcm)) as source:
        return source.getnframes() / source.getframerate()


def cut(
    pcm: Path, spans: Iterable[tuple[float, float]], directory: Path, padding: float = 0.2
) -> list[tuple[Path, float]]:
    """Writes one WAV per (start, end) span, padded on both sides.

    Returns (path, offset of the file in the source, in seconds) for each span.
    """
    with wave.open(str(pcm)) as source:
        rate, width = source.getframerate(), source.getsampwidth()
        frames = source.readframes(source.getnframes())
    directory.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, (start, end) in enumerate(spans):
        first = max(0, int((start - padding) * rate))
        last = int((end + padding) * rate)
        path = directory / f"{index:05d}.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(width)
            out.setframerate(rate)
            out.writeframes(frames[first * width : last * width])
        chunks.append((path, first / rate))
    return chunks
