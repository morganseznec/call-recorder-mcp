# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-27

20 languages instead of 2.

### Added

- **18 more interface languages**: Arabic, Chinese (Simplified and Traditional), Dutch, German, Hindi, Indonesian, Italian, Japanese, Korean, Portuguese (Brazil and Portugal), Russian, Spanish, Swahili, Thai, Turkish and Vietnamese, on top of English and French. Tool replies, transcript headings and speaker labels follow the language, with its own typography (full-width colon in Chinese and Japanese, space before the colon in French).
- **Permission prompts in the 20 languages** (one `.lproj` per language in the helper app). Run `call-recorder-setup` again to rebuild it.

### Changed

- **The language now comes from the Mac's preferred language** (`AppleLanguages`) instead of the region format (`AppleLocale`), so a Mac in English with a French region gets English. Chinese is told apart by script or region (`zh-Hant`, `zh-TW`, `zh-HK` give Traditional), Portuguese by region (`pt-PT` gives European Portuguese, anything else Brazilian).
- `CALL_RECORDER_LOCALE` accepts any of the 20 codes, in BCP 47 or POSIX form (`pt-PT`, `zh_TW`...).
- Messages moved from Python to one JSON catalog per language (`locales/`). Tests check that every catalog has the same messages and placeholders as English.
- The hallucination filter recognizes Whisper echoing the vocabulary prompt in every language.

## [1.1.0] - 2026-10-27

English and French, instead of French only.

### Added

- **English interface.** Tool replies, transcript headings and speaker labels ("Me", "Other side", "Speaker 1"...) now exist in English and French. The language follows the Mac's language (`AppleLocale`), and `CALL_RECORDER_LOCALE` (`en` or `fr`) overrides it; other languages fall back to English.
- **Localized permission prompts.** The macOS microphone and audio recording prompts for CallRecorder are in English, and in French through `fr.lproj` on a French Mac. Run `call-recorder-setup` again to rebuild the helper.

### Changed

- **The default transcription language follows the interface language** (it was always French). `CALL_RECORDER_LANGUAGE` still overrides it.
- Tool descriptions and server instructions are in English; Claude is told to answer in the user's language.
- An imported file transcribed with `speakers=1` has no speaker label.
- The Whisper hallucination filter also covers common English subtitle credits ("Thanks for watching").

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

[1.2.0]: https://github.com/morganseznec/call-recorder-mcp/releases/tag/v1.2.0
[1.1.0]: https://github.com/morganseznec/call-recorder-mcp/releases/tag/v1.1.0
[1.0.0]: https://github.com/morganseznec/call-recorder-mcp/releases/tag/v1.0.0
