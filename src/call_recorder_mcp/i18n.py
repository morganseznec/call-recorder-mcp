"""User-facing text, in English and French.

The language is CALL_RECORDER_LOCALE when set ("en", "fr", "fr_FR"...), else the
macOS language, else English. Transcription defaults to the same language
(CALL_RECORDER_LANGUAGE overrides it).
"""

import functools
import subprocess

from . import config

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        # Speaker labels
        "me": "Me",
        "remote": "Other side",
        "remote_n": "Speaker {n}",
        "person_n": "Speaker {n}",
        # Transcript
        "date": "Date",
        "source": "Source",
        "duration": "Duration",
        "participants": "Participants",
        "language": "Language",
        "transcript": "Transcript",
        "field": "- {label}: {value}",
        "turn": "**[{time}]{speaker}**: {text}",
        "no_speech": "_No speech detected._",
        "default_title": "{source} call",
        "glossary": "Glossary",
        # Tool replies
        "not_installed": "CallRecorder.app is not installed: run `call-recorder-setup`.",
        "already_recording": "A recording is already in progress: {name}. Stop it first with stop_recording.",
        "unknown_source": "Unknown source: {source}. Values: {values}, or a bundle ID.",
        "start_failed": "Could not start the recording: {error}",
        "waiting_permission": (
            "Recording launched ({name}) but macOS is still waiting for a permission (state: {state}). "
            "Accept the microphone and audio recording request for CallRecorder."
        ),
        "mic_missing": "the microphone is not recorded (permission denied?)",
        "app_only": "only {source} audio is captured on the other side",
        "recording": "Recording: {name}.",
        "notes": " Note: {notes}.",
        "no_recording": "No recording in progress.",
        "status_name": "Recording: {name}",
        "status_duration": "Duration: {duration}",
        "status_levels": "Levels (last 2 seconds): {levels}",
        "level_remote": "other side",
        "level_mic": "microphone",
        "status_apps": "Captured apps: {apps}",
        "status_no_apps": "none yet",
        "status_mic": "Microphone: {device}",
        "cancelled": (
            "Recording cancelled: it had not started, macOS was still waiting for the microphone permission "
            "for CallRecorder (System Settings > Privacy & Security)."
        ),
        "stopped": "Recording stopped ({duration}).",
        "stopped_at": "Recording stopped ({duration}): {folder}",
        "still_recording": "This recording is still in progress.",
        "files": "(Files: {folder})",
        "failed": "The transcription failed: {error}\nFolder: {folder}",
        "crashed": "The transcription stopped without a result. Log: {log}",
        "not_transcribed": 'Not transcribed yet. Use transcribe_recording("{name}").',
        "in_progress": "Transcription in progress (folder {name}). Call get_transcript in a minute to get the text.",
        "file_not_found": "File not found: {path}",
        "unreadable_file": "Cannot read this file: {error}",
        "no_transcript": "No transcript for this recording.",
        "unknown_speakers": "Unknown participants: {unknown}. Participants: {known}.",
        "no_recordings": "No recordings in {folder}.",
        "recording_not_found": "Recording not found: {reference}.",
        "folder": "Folder: {folder}",
        "state_recording": "recording",
        "state_none": "not transcribed",
        "state_queued": "queued",
        "state_running": "transcribing",
        "state_done": "transcribed",
        "state_error": "transcription failed",
        "state_crashed": "transcription failed",
        "sources_mic": "Microphone: {device}",
        "sources_output": "Output: {device}",
        "sources_apps": "Audio apps:",
        "sources_playing": ": playing",
        "sources_shortcuts": "Source shortcuts: {sources}",
    },
    "fr": {
        "me": "Moi",
        "remote": "Interlocuteur",
        "remote_n": "Interlocuteur {n}",
        "person_n": "Personne {n}",
        "date": "Date",
        "source": "Source",
        "duration": "Durée",
        "participants": "Participants",
        "language": "Langue",
        "transcript": "Transcription",
        "field": "- {label} : {value}",  # French puts a space before the colon
        "turn": "**[{time}]{speaker}** : {text}",
        "no_speech": "_Aucune parole détectée._",
        "default_title": "Conversation {source}",
        "glossary": "Vocabulaire",
        "not_installed": "CallRecorder.app n'est pas installé : lance `call-recorder-setup`.",
        "already_recording": "Un enregistrement est déjà en cours : {name}. Arrête-le d'abord avec stop_recording.",
        "unknown_source": "Source inconnue : {source}. Valeurs : {values}, ou un bundle ID.",
        "start_failed": "Échec du démarrage de l'enregistrement : {error}",
        "waiting_permission": (
            "Enregistrement lancé ({name}) mais macOS attend encore une autorisation (état : {state}). "
            "Accepte la demande d'accès au micro et à l'enregistrement audio pour CallRecorder."
        ),
        "mic_missing": "le micro n'est pas enregistré (autorisation refusée ?)",
        "app_only": "seul le son de {source} est capté côté interlocuteur",
        "recording": "Enregistrement en cours : {name}.",
        "notes": " Note : {notes}.",
        "no_recording": "Aucun enregistrement en cours.",
        "status_name": "Enregistrement en cours : {name}",
        "status_duration": "Durée : {duration}",
        "status_levels": "Niveaux (2 dernières secondes) : {levels}",
        "level_remote": "interlocuteur",
        "level_mic": "micro",
        "status_apps": "Applications captées : {apps}",
        "status_no_apps": "aucune pour l'instant",
        "status_mic": "Micro : {device}",
        "cancelled": (
            "Enregistrement annulé : il n'avait pas commencé, macOS attendait encore l'autorisation d'accès "
            "au micro pour CallRecorder (Réglages Système > Confidentialité et sécurité)."
        ),
        "stopped": "Enregistrement arrêté ({duration}).",
        "stopped_at": "Enregistrement arrêté ({duration}) : {folder}",
        "still_recording": "Cet enregistrement est encore en cours.",
        "files": "(Fichiers : {folder})",
        "failed": "La transcription a échoué : {error}\nDossier : {folder}",
        "crashed": "La transcription s'est arrêtée sans résultat. Journal : {log}",
        "not_transcribed": 'Pas encore transcrit. Utilise transcribe_recording("{name}").',
        "in_progress": (
            "Transcription en cours (dossier {name}). Appelle get_transcript dans une minute pour récupérer le texte."
        ),
        "file_not_found": "Fichier introuvable : {path}",
        "unreadable_file": "Impossible de lire ce fichier : {error}",
        "no_transcript": "Pas de transcription pour cet enregistrement.",
        "unknown_speakers": "Participants inconnus : {unknown}. Participants : {known}.",
        "no_recordings": "Aucun enregistrement dans {folder}.",
        "recording_not_found": "Enregistrement introuvable : {reference}.",
        "folder": "Dossier : {folder}",
        "state_recording": "en cours d'enregistrement",
        "state_none": "non transcrit",
        "state_queued": "en attente",
        "state_running": "transcription en cours",
        "state_done": "transcrit",
        "state_error": "transcription échouée",
        "state_crashed": "transcription échouée",
        "sources_mic": "Micro : {device}",
        "sources_output": "Sortie : {device}",
        "sources_apps": "Applications audio :",
        "sources_playing": " : joue du son",
        "sources_shortcuts": "Raccourcis de source : {sources}",
    },
}


@functools.cache
def _macos_locale() -> str:
    try:
        result = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def locale() -> str:
    """ "en" or "fr"."""
    code = (config.LOCALE or _macos_locale())[:2].lower()
    return code if code in MESSAGES else "en"


def language() -> str:
    """Default transcription language."""
    return config.LANGUAGE or locale()


def t(key: str, **values: object) -> str:
    return MESSAGES[locale()][key].format(**values)
