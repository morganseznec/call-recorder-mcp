import json
import struct
import wave

import numpy as np
import pytest

from call_recorder_mcp import live, server, storage


def apple_style_wav(path, rate=48_000):
    """Header like AVAudioFile's: JUNK and FLLR chunks, data size not filled in yet."""
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    chunks = b"JUNK" + struct.pack("<I", 28) + bytes(28)
    chunks += b"fmt " + struct.pack("<I", 16) + fmt
    chunks += b"FLLR" + struct.pack("<I", 8) + bytes(8)
    chunks += b"data" + struct.pack("<I", 0)
    path.write_bytes(b"RIFF" + struct.pack("<I", 0) + b"WAVE" + chunks)


def append_samples(path, samples):
    with path.open("ab") as file:
        file.write((np.asarray(samples) * 32767).astype("<i2").tobytes())


def test_growing_wav_reads_only_new_samples_at_16k(tmp_path):
    path = tmp_path / "remote.wav"
    reader = live.GrowingWav(path)
    assert len(reader.read()) == 0  # file not created yet

    apple_style_wav(path)
    append_samples(path, np.full(4800, 0.5))  # 0.1 s at 48 kHz
    first = reader.read()
    assert len(first) == 1600
    assert first == pytest.approx(0.5, abs=1e-3)

    append_samples(path, np.full(4801, -0.25))  # an incomplete group of 3 is kept for later
    assert len(reader.read()) == 1600
    append_samples(path, np.full(2, -0.25))
    assert len(reader.read()) == 1
    assert len(reader.read()) == 0


def test_growing_wav_reads_standard_files(tmp_path):
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(np.zeros(800, dtype="<i2").tobytes())
    assert len(live.GrowingWav(path).read()) == 800


def test_mic_echo_of_the_other_side_is_detected():
    remote = [{"start": 10.0, "text": "La livraison est prévue fin novembre."}]
    assert live.is_echo("la livraison est prévue fin novembre", 10.4, remote)
    assert not live.is_echo("la livraison est prévue fin novembre", 30.0, remote)
    assert not live.is_echo("D'accord, merci.", 10.4, remote)


def write_live(folder, lines, state="running", pid=None):
    with (folder / live.LINES_FILE).open("w") as output:
        for line in lines:
            output.write(json.dumps(line, ensure_ascii=False) + "\n")
    storage.write_json(folder / live.STATUS_FILE, {"state": state, "pid": pid})


def test_get_live_transcript_returns_new_lines_and_cursor():
    import os

    folder = storage.create("call")
    lines = [
        {"seq": 0, "start": 1.0, "end": 3.0, "speaker": "Moi", "text": "Quel est le planning ?"},
        {"seq": 1, "start": 4.0, "end": 8.0, "speaker": "Interlocuteur", "text": "Fin novembre, en principe."},
    ]
    write_live(folder, lines, pid=os.getpid())
    reply = server.get_live_transcript(since=0, wait_seconds=0)
    assert "**[00:00:01] Moi** : Quel est le planning ?" in reply
    assert "since=2" in reply
    reply = server.get_live_transcript(since=1, wait_seconds=0)
    assert "Quel est le planning" not in reply and "Fin novembre" in reply
    assert "Rien de nouveau" in server.get_live_transcript(since=2, wait_seconds=0)


def test_get_live_transcript_reports_the_end_and_off_state():
    folder = storage.create("call")
    assert "pas activée" in server.get_live_transcript()
    write_live(folder, [], state="ended")
    assert "terminé" in server.get_live_transcript(wait_seconds=5)


def test_dead_worker_is_reported():
    folder = storage.create("call")
    write_live(folder, [], state="running", pid=999_999)
    assert live.status(folder)["state"] == "error"


def test_utterances_get_audio_from_before_their_start():
    track = live.Track.__new__(live.Track)  # no VAD needed for the lead-in logic
    track.history = np.zeros(0, dtype=np.float32)
    track.history_start = 0
    track._remember(np.arange(32_000, dtype=np.float32))  # 2 s at 16 kHz
    start, samples = track._with_lead_in(16_000, np.arange(16_000, 17_000, dtype=np.float32))
    assert start == pytest.approx(1.0 - live.Track.LEAD_IN)
    assert samples[0] == 16_000 - int(live.Track.LEAD_IN * 16_000)
    assert len(samples) == 1_000 + int(live.Track.LEAD_IN * 16_000)


def test_history_is_bounded():
    track = live.Track.__new__(live.Track)
    track.history = np.zeros(0, dtype=np.float32)
    track.history_start = 0
    for _ in range(40):
        track._remember(np.zeros(16_000, dtype=np.float32))
    assert len(track.history) == int(live.Track.HISTORY * 16_000)
    assert track.history_start == 40 * 16_000 - len(track.history)
