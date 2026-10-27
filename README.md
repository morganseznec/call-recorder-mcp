# Call Recorder MCP

[Model Context Protocol](https://modelcontextprotocol.io/) server that records calls on macOS (WhatsApp, Teams, Zoom, Slack, FaceTime, Google Meet or any meeting in the browser) and transcribes them locally with [Whisper](https://github.com/ggml-org/whisper.cpp). Ask Claude to record, say when the call is over, and get the transcript back in the conversation, with who said what.

Nothing leaves your Mac: capture, transcription and speaker separation all run locally.

## Features

- **Records both sides of a call** as two aligned tracks: your microphone and what the apps play. Uses Core Audio process taps (macOS 14.4+), so no virtual audio driver such as BlackHole is needed.
- **Per-app capture**: record only WhatsApp, Teams or the browser, or the whole system output. The capture follows the app if it starts playing late, and survives switching to AirPods mid-call.
- **Local transcription** with whisper.cpp (`large-v3-turbo`, Metal accelerated) and Silero voice activity detection, which keeps Whisper from inventing text during silences. About 75 s for 18 minutes of call on an M-series Mac.
- **Who said what**: "Moi" (your mic) and "Interlocuteur" (the other side). Without headphones your mic also hears the other side; those echoes are removed.
- **Several people on the other side**: give their number and their voices are separated (Interlocuteur 1, 2...), then renamed to real names in one call.
- **Custom vocabulary** so product, company and people names are spelled right.
- **Works with existing files** too: WhatsApp voice notes, meeting recordings, any audio or video format ffmpeg reads.
- A **mixed `conversation.wav`** of each call, to listen to.

Tool replies are in French (the server was built for French-speaking calls); transcription works in any language Whisper supports.

## Requirements

- macOS 14.4 or later. Apple Silicon recommended.
- [Homebrew](https://brew.sh) packages: `brew install whisper-cpp ffmpeg uv`
- Xcode Command Line Tools (to compile the capture helper): `xcode-select --install`
- About 650 MB of disk space for the models.

## Quick install

1. Build the capture helper and download the models (run it again after each upgrade):

   ```bash
   uvx --from git+https://github.com/morganseznec/call-recorder-mcp call-recorder-setup
   ```

2. Register the server in Claude Code:

   ```bash
   claude mcp add call-recorder -s user -- uvx --from git+https://github.com/morganseznec/call-recorder-mcp call-recorder-mcp
   ```

   For Claude Desktop, add the same command to `~/Library/Application Support/Claude/claude_desktop_config.json` and restart it:

   ```json
   {
     "mcpServers": {
       "call-recorder": {
         "command": "/opt/homebrew/bin/uvx",
         "args": ["--from", "git+https://github.com/morganseznec/call-recorder-mcp", "call-recorder-mcp"]
       }
     }
   }
   ```

3. On the first recording, macOS asks whether **CallRecorder** may use the microphone and record audio from other apps. Accept both. If you missed the prompt: System Settings > Privacy & Security > Microphone (and Screen & System Audio Recording).

`call-recorder-setup` signs the helper with your first "Apple Development" certificate if you have one, so macOS keeps the permissions across upgrades. Without one it uses an ad-hoc signature, and macOS asks again after each rebuild.

## Using it from Claude

You never call the tools yourself: talk to Claude and it picks them.

| What you say | What happens |
|---|---|
| "Enregistre mon appel WhatsApp" | `start_recording(source="whatsapp")` |
| "C'est fini, transcris" | `stop_recording()` returns the transcript |
| "Enregistre la réunion Teams, ils sont 4 en face" | `start_recording(source="teams", speakers=4)` |
| "Le 1 c'est Alice, le 2 c'est Bob" | `rename_speakers({"Interlocuteur 1": "Alice", ...})` |
| "Transcris cette note vocale ~/Downloads/note.opus" | `transcribe_file(path=...)` |
| "Retranscris le dernier appel en anglais" | `transcribe_recording(language="en")` |
| "Est-ce que ça enregistre bien ?" | `recording_status()` shows live levels for both sides |

A transcript looks like this:

```markdown
# Point produit

- Date : 2026-10-27 12:10:37
- Source : teams
- Durée : 00:18:44
- Participants : Alice, Interlocuteur 2, Moi
- Langue : fr

## Transcription

**[00:00:02] Alice** : Bonjour à tous, on fait le point sur la version 2.

**[00:00:06] Moi** : De mon côté la version iOS est prête, elle est dans TestFlight.
```

## How it works

```
Claude ──MCP──> call-recorder-mcp ──open──> CallRecorder.app ──> mic.wav + remote.wav
                       │
                       └──> background job: VAD or diarization ──> whisper.cpp ──> transcript.md
```

- **Capture.** `CallRecorder.app` is a small Swift helper ([`macos/main.swift`](src/call_recorder_mcp/macos/main.swift)). It taps the audio of the chosen processes (Core Audio process tap + private aggregate device) and records the microphone (AVAudioEngine), writing two mono 48 kHz tracks kept aligned on the host clock. It is launched through `open` so that macOS asks for the permissions under its own name, whichever MCP client started the server.
- **WhatsApp** calls go through CallKit: their audio is played by Apple's `avconferenced` service, not by WhatsApp itself. The `whatsapp` source includes it.
- **Transcription** runs in a detached process, so a long call never times out a tool call: `stop_recording` waits up to 30 s, then Claude polls `get_transcript`. Each track is cut into utterances and every utterance is transcribed as its own short file, in a single whisper.cpp run. This keeps timestamps exact and lets the vocabulary apply everywhere.
- **Speaker separation** uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx): pyannote segmentation 3.0 and CAM++ voice embeddings.

### Speaker separation: why you give the number of people

Measured on a real 18-minute WhatsApp call between two people, both sides mixed into one file:

| Setup | Result |
|---|---|
| CAM++ embeddings, number of speakers given | **99.7 %** of speaking time attributed to the right person |
| WeSpeaker ResNet34 embeddings, number given | about 70 % |
| Automatic speaker count, any threshold tried | one person split into 2 to 12 "voices", or distinct voices merged |

Short replies ("ok", "uh-huh") are too brief for a reliable voice signature, so automatic counting over-splits on real calls. With the number of people given, the clustering is reliable. Claude asks you for it on a group call.

Limits: very short interjections and overlapping speech can be misattributed, and two very similar voices can be merged.

## Vocabulary

Whisper misspells names it does not know. List them in `~/Library/Application Support/call-recorder-mcp/vocabulary.txt`, one per line (`#` for comments). `call-recorder-setup` creates it from [an example](src/call_recorder_mcp/vocabulary.example.txt).

Only list words people actually say: Whisper tends to insert listed names where it hears something close.

## Configuration

All optional, set with `-e NAME=value` in `claude mcp add`:

| Variable | Default | Purpose |
|---|---|---|
| `CALL_RECORDER_DIR` | `~/CallRecordings` | Where recordings and transcripts go |
| `CALL_RECORDER_LANGUAGE` | `fr` | Default transcription language (`en`, `auto`...) |
| `CALL_RECORDER_VOCABULARY` | `<home>/vocabulary.txt` | Vocabulary file |
| `CALL_RECORDER_HOME` | `~/Library/Application Support/call-recorder-mcp` | Helper app, models, vocabulary |
| `CALL_RECORDER_WHISPER_MODEL` | `<home>/models/ggml-large-v3-turbo-q5_0.bin` | Any whisper.cpp model |
| `CALL_RECORDER_SIGN_IDENTITY` | first Apple Development identity | Code signing identity used by `call-recorder-setup` |

Each recording gets its own folder, `<CALL_RECORDER_DIR>/<date>_<time>_<title>/`:

| File | Content |
|---|---|
| `transcript.md`, `transcript.json` | The transcript, for people and for programs |
| `conversation.wav` | Both sides mixed |
| `mic.wav`, `remote.wav` | Your microphone, the other side (or `audio.wav` for an imported file) |
| `meta.json`, `capture.json`, `transcription.json` | Title, settings and state |

Recordings stop automatically after 4 hours.

## Tool reference

| Tool | Purpose |
|---|---|
| `start_recording(source, title, include_mic, language, speakers)` | Start recording. `source`: `system` (default), `whatsapp`, `teams`, `zoom`, `slack`, `facetime`, `browser`, `chrome`, `safari`, `firefox`, `arc`, `edge`, or a bundle ID prefix |
| `recording_status()` | Elapsed time, captured apps, live levels (-120 dB on a side means nothing is captured) |
| `stop_recording(transcribe, language, speakers, wait_seconds)` | Stop, mix `conversation.wav`, start the transcription and return it when ready |
| `get_transcript(recording, wait_seconds)` | Transcript of a recording (`latest` or folder name), waiting if still running |
| `transcribe_recording(recording, language, speakers, wait_seconds)` | Transcribe again, e.g. in another language or to separate voices |
| `transcribe_file(path, title, language, speakers, wait_seconds)` | Transcribe an existing audio or video file |
| `rename_speakers(names, recording)` | Replace "Interlocuteur 1"... with real names |
| `list_recordings(limit)` | Latest recordings and their transcription state |
| `list_audio_sources()` | Apps currently using audio, default microphone and output |

## Privacy and consent

Recording a conversation without the other people's consent is illegal in many countries (in France, article 226-1 of the Code pénal). Tell the people you are recording. Audio and transcripts stay on your Mac, in the folder above.

## Development

```bash
git clone https://github.com/morganseznec/call-recorder-mcp
cd call-recorder-mcp
uv run --extra dev pytest
uv run --extra dev ruff check . && uv run --extra dev ruff format --check .
uv run call-recorder-setup --skip-models   # rebuild the helper after editing main.swift
```

To run your checkout in Claude Code instead of the published version:

```bash
claude mcp add call-recorder -s user -- uv run --directory "$PWD" call-recorder-mcp
```

## Versioning and changelog

[Semantic Versioning](https://semver.org/). See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
