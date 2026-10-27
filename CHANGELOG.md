# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-27

First public release.

### Added

- **Call recording on macOS** through `CallRecorder.app`, a Swift helper using Core Audio process taps (macOS 14.4+, no virtual audio driver). Records your microphone and the other side as two mono 48 kHz tracks aligned on the host clock, plus a mixed `conversation.wav`.
- **Sources**: the whole system output, or one app: WhatsApp (including its CallKit audio, played by `avconferenced`), Teams, Zoom, Slack, FaceTime, Chrome, Safari, Firefox, Arc, Edge, all browsers, or any bundle ID prefix. The per-app tap follows the app when it starts playing late and is rebuilt when the output device changes (AirPods connecting mid-call).
- **Local transcription** with whisper.cpp (`large-v3-turbo`) in a background job, so long calls never time out a tool call. Each utterance found by the Silero VAD is transcribed as its own short file in a single whisper.cpp run, which keeps timestamps exact.
- **Speaker labels**: "Moi" for the microphone, "Interlocuteur" for the other side. Mic echoes of the other side (no headphones) are removed.
- **Speaker separation** for several people on the other side (or in an imported file) with sherpa-onnx: pyannote segmentation 3.0 and CAM++ embeddings, 99.7 % of speaking time attributed correctly on a real two-person WhatsApp call. The number of speakers is given by the user: automatic counting was unreliable on real calls.
- **`rename_speakers`** to replace "Interlocuteur 1"... with real names.
- **Custom vocabulary** file passed to Whisper as a prompt, so names are spelled right.
- **`transcribe_file`** for existing audio and video files (voice notes, meeting recordings).
- **Live levels** in `recording_status`, to spot a silent side (missing permission, wrong source) during the call.
- **`call-recorder-setup`**: checks the tools, compiles and signs the helper (Apple Development identity when available, so macOS keeps the permissions across upgrades), downloads the models and installs the example vocabulary.
- Filters for common Whisper hallucinations on silence and noise (subtitle credits).
- Test suite (unit tests and a JSON-RPC stdio roundtrip) and CI.

[1.0.0]: https://github.com/morganseznec/call-recorder-mcp/releases/tag/v1.0.0
