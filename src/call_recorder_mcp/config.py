"""Paths and settings. Everything can be overridden with environment variables.

Modules read these attributes at call time (``config.RECORDINGS``, not
``from .config import RECORDINGS``) so tests can monkeypatch them.
"""

import os
from pathlib import Path


def _path(variable: str, default: str | Path) -> Path:
    return Path(os.environ.get(variable) or default).expanduser()


# Installed app, models and the user's vocabulary.
HOME = _path("CALL_RECORDER_HOME", "~/Library/Application Support/call-recorder-mcp")
# One folder per recording.
RECORDINGS = _path("CALL_RECORDER_DIR", "~/CallRecordings")
VOCABULARY = _path("CALL_RECORDER_VOCABULARY", HOME / "vocabulary.txt")
# Interface language ("en" or "fr"); empty = the macOS language. See i18n.py.
LOCALE = os.environ.get("CALL_RECORDER_LOCALE", "")
# Transcription language ("fr", "en", "auto"...); empty = the interface language.
LANGUAGE = os.environ.get("CALL_RECORDER_LANGUAGE", "")
SIGN_IDENTITY = os.environ.get("CALL_RECORDER_SIGN_IDENTITY", "")

APP = HOME / "CallRecorder.app"
APP_BINARY = APP / "Contents" / "MacOS" / "CallRecorder"

MODELS = HOME / "models"
WHISPER_MODEL = _path("CALL_RECORDER_WHISPER_MODEL", MODELS / "ggml-large-v3-turbo-q5_0.bin")
VAD_MODEL = MODELS / "ggml-silero-v6.2.0.bin"
SEGMENTATION_MODEL = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
# CAM++ voice embeddings: 96 % right on a real WhatsApp call, where WeSpeaker got 70 %.
EMBEDDING_MODEL = MODELS / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"

# Model file -> download URL (a .tar.bz2 URL is extracted into MODELS).
DOWNLOADS = {
    WHISPER_MODEL: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
    VAD_MODEL: "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
    SEGMENTATION_MODEL: "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    EMBEDDING_MODEL: "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
}


def extend_path() -> None:
    """MCP clients such as Claude Desktop start servers with a minimal PATH."""
    extra = [p for p in ("/opt/homebrew/bin", "/usr/local/bin") if p not in os.environ.get("PATH", "")]
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *extra])
