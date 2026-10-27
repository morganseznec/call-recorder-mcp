"""Live transcription during a call, so Claude can follow it and help in real time.

A detached worker (``python -m call_recorder_mcp.live FOLDER LANGUAGE``) reads
mic.wav and remote.wav while CallRecorder.app is still writing them, cuts each
side into utterances with a streaming VAD (sherpa-onnx Silero) and sends every
finished utterance to a whisper-server that keeps the model in memory. Text
arrives about a second after each sentence ends, appended to ``live.jsonl``.

This is a draft for following the call: no speaker separation, less context
per sentence. The final transcript, made after the call, is unchanged.
"""

import difflib
import io
import json
import os
import socket
import struct
import subprocess
import sys
import time
import traceback
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

from . import audio, capture, config, storage
from .i18n import t
from .transcription import is_hallucination, vocabulary_prompt

LINES_FILE = "live.jsonl"
STATUS_FILE = "live.json"
POLL_INTERVAL = 0.3  # seconds between reads of the growing tracks


class GrowingWav:
    """Reads the samples appended to a WAV file that is still being written.

    The header's data size is only written when the file is closed, so the
    readable length comes from the file size. Returns 16 kHz mono float32.
    """

    def __init__(self, path: Path):
        self.path = path
        self.position: int | None = None  # next byte to read
        self.rate = 0
        self.channels = 1
        self.pending = np.zeros(0, dtype=np.float32)  # samples left over by resampling

    def _parse_header(self) -> bool:
        try:
            header = self.path.open("rb").read(16384)
        except OSError:
            return False
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return False
        position = 12
        while position + 8 <= len(header):
            chunk, size = header[position : position + 4], struct.unpack("<I", header[position + 4 : position + 8])[0]
            if chunk == b"fmt ":
                encoding, self.channels, self.rate = struct.unpack("<HHI", header[position + 8 : position + 16])
                bits = struct.unpack("<H", header[position + 22 : position + 24])[0]
                if encoding != 1 or bits != 16:
                    raise ValueError(f"{self.path.name}: expected 16-bit PCM")
            elif chunk == b"data":
                self.position = position + 8
                return self.rate > 0
            position += 8 + size + (size & 1)
        return False

    def read(self) -> np.ndarray:
        if self.position is None and not self._parse_header():
            return np.zeros(0, dtype=np.float32)
        assert self.position is not None
        frame_bytes = 2 * self.channels
        available = (self.path.stat().st_size - self.position) // frame_bytes * frame_bytes
        if available <= 0:
            return np.zeros(0, dtype=np.float32)
        with self.path.open("rb") as file:
            file.seek(self.position)
            raw = file.read(available)
        self.position += len(raw)
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        if self.channels > 1:
            samples = samples.reshape(-1, self.channels).mean(axis=1)
        return self._to_16k(samples)

    def _to_16k(self, samples: np.ndarray) -> np.ndarray:
        if self.rate == audio.SAMPLE_RATE:
            return samples
        if self.rate % audio.SAMPLE_RATE:
            count = int(len(samples) * audio.SAMPLE_RATE / self.rate)
            return np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples).astype(
                np.float32
            )
        # 48 kHz -> 16 kHz: average each group of 3 samples (a crude low-pass, fine for speech).
        factor = self.rate // audio.SAMPLE_RATE
        samples = np.concatenate([self.pending, samples])
        usable = len(samples) // factor * factor
        self.pending = samples[usable:]
        return samples[:usable].reshape(-1, factor).mean(axis=1)


class WhisperServer:
    """whisper-server on a free local port, model loaded once for the whole call."""

    def __init__(self, language: str):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.language = language
        self.prompt = vocabulary_prompt()
        command = [
            "whisper-server",
            "-m", str(config.WHISPER_MODEL),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-l", language,
            "-t", str(min(8, os.cpu_count() or 4)),
        ]  # fmt: skip
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=1)
                return
            except OSError:
                if self.process.poll() is not None:
                    break
                time.sleep(0.2)
        self.close()
        raise RuntimeError("whisper-server did not start")

    def transcribe(self, samples: np.ndarray) -> str:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(audio.SAMPLE_RATE)
            out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        fields = {"response_format": "json", "language": self.language, "temperature": "0"}
        if self.prompt:
            fields["prompt"] = self.prompt
        boundary = uuid.uuid4().hex
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="utterance.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        body += buffer.getvalue() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/inference",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read()).get("text", "").strip()

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


class Track:
    """One side of the call: its growing file, its VAD and its speaker label."""

    # The VAD starts a segment right at the speech onset, which clips the first word:
    # each utterance gets this much audio from before it.
    LEAD_IN = 0.3
    HISTORY = 30.0  # seconds kept to take the lead-in from (longer than any utterance)

    def __init__(self, path: Path, speaker: str):
        import sherpa_onnx

        self.reader = GrowingWav(path)
        self.speaker = speaker
        self.history = np.zeros(0, dtype=np.float32)
        self.history_start = 0  # index, in the whole track, of history[0]
        self.vad = sherpa_onnx.VoiceActivityDetector(
            sherpa_onnx.VadModelConfig(
                silero_vad=sherpa_onnx.SileroVadModelConfig(
                    model=str(config.LIVE_VAD_MODEL), min_silence_duration=0.5, max_speech_duration=20
                ),
                sample_rate=audio.SAMPLE_RATE,
            ),
            buffer_size_in_seconds=60,
        )

    def utterances(self, final: bool = False) -> list[tuple[float, np.ndarray]]:
        """(start in seconds, samples) of each utterance finished since the last call."""
        samples = self.reader.read()
        if len(samples):
            self.vad.accept_waveform(samples)
            self._remember(samples)
        if final:
            self.vad.flush()
        finished = []
        while not self.vad.empty():
            segment = self.vad.front
            finished.append(self._with_lead_in(segment.start, np.array(segment.samples, dtype=np.float32)))
            self.vad.pop()
        return finished

    def _remember(self, samples: np.ndarray) -> None:
        self.history = np.concatenate([self.history, samples])
        excess = len(self.history) - int(self.HISTORY * audio.SAMPLE_RATE)
        if excess > 0:
            self.history = self.history[excess:]
            self.history_start += excess

    def _with_lead_in(self, start: int, samples: np.ndarray) -> tuple[float, np.ndarray]:
        first = max(self.history_start, start - int(self.LEAD_IN * audio.SAMPLE_RATE))
        if first < start:
            lead_in = self.history[first - self.history_start : start - self.history_start]
            samples = np.concatenate([lead_in, samples])
        return first / audio.SAMPLE_RATE, samples


def is_echo(text: str, start: float, remote_lines: list[dict[str, Any]]) -> bool:
    """Without headphones the mic hears the other side: same words at the same time."""
    return any(
        abs(line["start"] - start) < 3
        and difflib.SequenceMatcher(None, text.lower(), line["text"].lower()).ratio() > 0.6
        for line in remote_lines[-10:]
    )


def read_lines(folder: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in (folder / LINES_FILE).read_text().splitlines() if line]
    except OSError:
        return []


def status(folder: Path) -> dict[str, Any]:
    state = storage.read_json(folder / STATUS_FILE)
    if state.get("state") == "running" and not capture.process_alive(state.get("pid")):
        return {**state, "state": "error", "error": state.get("error") or "worker stopped"}
    return state


def start(folder: Path, language: str) -> None:
    storage.write_json(folder / STATUS_FILE, {"state": "starting"})
    with open(folder / "live.log", "w") as log:
        subprocess.Popen(
            [sys.executable, "-m", "call_recorder_mcp.live", str(folder), language],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


def run(folder: Path, language: str) -> None:
    status_path = folder / STATUS_FILE
    storage.write_json(status_path, {"state": "running", "pid": os.getpid()})
    server = WhisperServer(language)
    try:
        tracks = {
            "remote": Track(folder / "remote.wav", t("remote")),  # first: a mic echo is then recognized
            "mic": Track(folder / "mic.wav", t("me")),
        }
        remote_lines: list[dict[str, Any]] = []
        count = len(read_lines(folder))
        with (folder / LINES_FILE).open("a") as output:
            while True:
                recorder = capture.state(folder)
                # Once the capture has stopped, read what is left and flush the last utterance.
                final = recorder.get("state") in ("stopped", "error") or not capture.process_alive(recorder.get("pid"))
                for name, track in tracks.items():
                    for begin, samples in track.utterances(final):
                        text = server.transcribe(samples)
                        if not text or is_hallucination(text):
                            continue
                        if name == "mic" and is_echo(text, begin, remote_lines):
                            continue
                        line = {
                            "seq": count,
                            "start": round(begin, 2),
                            "end": round(begin + len(samples) / audio.SAMPLE_RATE, 2),
                            "speaker": track.speaker,
                            "text": text,
                        }
                        output.write(json.dumps(line, ensure_ascii=False) + "\n")
                        output.flush()
                        count += 1
                        if name == "remote":
                            remote_lines.append(line)
                if final:
                    break
                time.sleep(POLL_INTERVAL)
    except Exception as error:
        storage.write_json(status_path, {"state": "error", "error": str(error)[-500:]})
        traceback.print_exc()
        raise SystemExit(1) from error
    finally:
        server.close()
    storage.write_json(status_path, {"state": "ended"})


if __name__ == "__main__":
    config.extend_path()
    run(Path(sys.argv[1]), sys.argv[2])
