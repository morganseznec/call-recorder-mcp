"""MCP server: the tools Claude calls. Replies are in French, for the user.

Tools are synchronous: MCP SDK v2 runs them on worker threads, so waiting for a
capture or a transcription never blocks the server.
"""

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__, audio, capture, config, jobs, storage, transcription

INSTRUCTIONS = (
    "Enregistre une conversation audio sur ce Mac (appel WhatsApp, Teams, Zoom, réunion dans le "
    "navigateur...) puis la transcrit localement avec Whisper. Flux : start_recording, puis "
    "stop_recording quand l'utilisateur dit que l'appel est fini (renvoie la transcription). "
    "Moi = micro de l'utilisateur, Interlocuteur = son joué par les applications. "
    "Appel de groupe : passe le nombre de personnes de l'autre côté (speakers) pour séparer leurs "
    "voix en Interlocuteur 1, 2... ; demande-le à l'utilisateur si tu ne le connais pas. "
    "Ensuite rename_speakers remplace ces numéros par les vrais noms."
)
NOT_INSTALLED = "CallRecorder.app n'est pas installé : lance `call-recorder-setup`."

mcp = MCPServer("call-recorder", version=__version__, instructions=INSTRUCTIONS)


def _transcript(folder: Path, wait_seconds: float) -> str:
    """The transcript, or where the job stands after waiting up to wait_seconds."""
    state, error = jobs.wait(folder, wait_seconds)
    if state is jobs.Status.DONE:
        return f"{(folder / 'transcript.md').read_text()}\n(Fichiers : {folder})"
    if state is jobs.Status.ERROR:
        return f"La transcription a échoué : {error}\nDossier : {folder}"
    if state is jobs.Status.CRASHED:
        return f"La transcription s'est arrêtée sans résultat. Journal : {folder / 'transcription.log'}"
    if state is jobs.Status.NONE:
        return f'Pas encore transcrit. Utilise transcribe_recording("{folder.name}").'
    return (
        f"Transcription en cours (dossier {folder.name}). "
        "Appelle get_transcript dans une minute pour récupérer le texte."
    )


def _transcribe(folder: Path, language: str, speakers: int, wait_seconds: float) -> str:
    meta = storage.update_meta(folder, speakers=speakers) if speakers > 0 else storage.read_meta(folder)
    jobs.start(folder, language or meta.get("language") or config.LANGUAGE)
    return _transcript(folder, wait_seconds)


@mcp.tool()
def start_recording(
    source: str = "system", title: str = "", include_mic: bool = True, language: str = "", speakers: int = 1
) -> str:
    """Commence à enregistrer un appel ou une conversation.

    source: "system" (tout le son du Mac, le plus sûr), "whatsapp", "teams", "zoom", "slack",
      "facetime", "browser" (tous les navigateurs), "chrome", "safari", "firefox", "arc", "edge",
      ou un préfixe de bundle ID (ex. "com.microsoft.teams2").
    title: nom de la conversation (sert au nom du dossier).
    include_mic: enregistrer aussi le micro (votre voix).
    language: langue pour la transcription ("fr", "en", "auto"...).
    speakers: nombre de personnes de l'autre côté (1 par défaut). Au-delà de 1, leurs voix
      sont séparées dans la transcription (Interlocuteur 1, Interlocuteur 2...).
    """
    if not capture.is_installed():
        return NOT_INSTALLED
    if current := capture.active():
        return f"Un enregistrement est déjà en cours : {current.name}. Arrête-le d'abord avec stop_recording."
    try:
        key, bundles = capture.resolve_source(source)
    except ValueError as error:
        return str(error)

    folder = storage.create(
        title or key,
        title=title or f"Conversation {key}",
        source=key,
        bundles=bundles,
        language=language or config.LANGUAGE,
        speakers=max(1, speakers),
    )
    state = capture.start(folder, bundles, include_mic)
    if state.get("state") == "error":
        return f"Échec du démarrage : {state.get('error')}"
    if state.get("state") != "recording":
        return (
            f"Enregistrement lancé ({folder.name}) mais il attend encore une autorisation macOS "
            f"(état : {state.get('state', 'inconnu')}). Accepte la demande d'accès au micro et à l'audio."
        )
    notes = []
    if include_mic and not state.get("mic"):
        notes.append("le micro n'est pas enregistré (autorisation refusée ?)")
    if bundles:
        notes.append(f"seul le son de {key} est capté côté interlocuteur")
    return f"Enregistrement en cours : {folder.name}." + (f" Note : {'; '.join(notes)}." if notes else "")


@mcp.tool()
def recording_status() -> str:
    """Indique si un enregistrement est en cours, depuis combien de temps, et les niveaux sonores
    récents (un niveau à -120 dB côté interlocuteur veut dire qu'aucun son n'est capté)."""
    folder = capture.active()
    if not folder:
        return "Aucun enregistrement en cours."
    state = capture.state(folder)
    levels = [
        f"{label} {state[key]:.0f} dB"
        for key, label in (("remote_peak_db", "interlocuteur"), ("mic_peak_db", "micro"))
        if key in state
    ]
    lines = [
        f"Enregistrement en cours : {folder.name}",
        f"Durée : {storage.format_duration(state.get('elapsed_seconds', 0))}",
        f"Niveaux (2 dernières secondes) : {', '.join(levels) or 'n/a'}",
    ]
    if "remote_sources" in state:
        lines.append(f"Applications captées : {', '.join(state['remote_sources']) or 'aucune pour l’instant'}")
    if state.get("mic_device"):
        lines.append(f"Micro : {state['mic_device']}")
    return "\n".join(lines)


@mcp.tool()
def stop_recording(transcribe: bool = True, language: str = "", speakers: int = 0, wait_seconds: int = 30) -> str:
    """Arrête l'enregistrement en cours et lance la transcription.

    speakers: nombre de personnes de l'autre côté, s'il diffère de celui donné au démarrage
      (0 = inchangé). Pour un appel de groupe, demande-le à l'utilisateur.
    language: remplace la langue choisie au démarrage.
    Renvoie la transcription si elle est prête en moins de wait_seconds, sinon indique
    d'appeler get_transcript plus tard.
    """
    folder = capture.active()
    if not folder:
        return "Aucun enregistrement en cours."
    started = capture.state(folder).get("state") == "recording"
    duration = storage.format_duration(capture.stop(folder).get("duration_seconds", 0))
    if not started:
        return (
            "Enregistrement annulé : il n'avait pas commencé, macOS attendait encore l'autorisation "
            "d'accès au micro pour CallRecorder (Réglages Système > Confidentialité et sécurité)."
        )
    if not transcribe:
        return f"Enregistrement arrêté ({duration}) : {folder}"
    return f"Enregistrement arrêté ({duration}).\n\n" + _transcribe(folder, language, speakers, wait_seconds)


@mcp.tool()
def get_transcript(recording: str = "latest", wait_seconds: int = 30) -> str:
    """Renvoie la transcription d'un enregistrement ("latest" ou le nom du dossier),
    en attendant jusqu'à wait_seconds si elle est encore en cours."""
    try:
        folder = storage.resolve(recording)
    except ValueError as error:
        return str(error)
    if folder == capture.active():
        return "Cet enregistrement est encore en cours."
    return _transcript(folder, wait_seconds)


@mcp.tool()
def transcribe_recording(
    recording: str = "latest", language: str = "", speakers: int = 0, wait_seconds: int = 30
) -> str:
    """(Re)transcrit un enregistrement existant, par exemple dans une autre langue ou pour
    séparer les voix : speakers = nombre de personnes de l'autre côté (0 = inchangé)."""
    try:
        folder = storage.resolve(recording)
    except ValueError as error:
        return str(error)
    if folder == capture.active():
        return "Cet enregistrement est encore en cours."
    return _transcribe(folder, language, speakers, wait_seconds)


@mcp.tool()
def transcribe_file(path: str, title: str = "", language: str = "", speakers: int = 1, wait_seconds: int = 30) -> str:
    """Transcrit un fichier audio ou vidéo existant (note vocale WhatsApp .opus, .m4a, .mp3,
    .mp4, enregistrement Teams...), copié dans un nouveau dossier d'enregistrement.

    speakers: nombre total de personnes qui parlent dans le fichier (au-delà de 1, leurs voix
      sont séparées en Personne 1, Personne 2...).
    """
    source = Path(path).expanduser()
    if not source.is_file():
        return f"Fichier introuvable : {path}"
    folder = storage.create(
        title or source.stem,
        title=title or source.name,
        source=f"fichier {source}",
        language=language or config.LANGUAGE,
        speakers=max(1, speakers),
    )
    try:
        audio.import_file(source, folder / "audio.wav")
    except ValueError as error:
        return str(error)
    return _transcribe(folder, language, 0, wait_seconds)


@mcp.tool()
def rename_speakers(names: dict[str, str], recording: str = "latest") -> str:
    """Remplace les noms des participants dans une transcription, par exemple
    {"Interlocuteur 1": "Alice", "Interlocuteur 2": "Bob"}. Renvoie la transcription mise à jour."""
    try:
        folder = storage.resolve(recording)
        transcription.rename_speakers(folder, names)
    except ValueError as error:
        return str(error)
    return _transcript(folder, 0)


@mcp.tool()
def list_recordings(limit: int = 10) -> str:
    """Liste les derniers enregistrements avec leur état de transcription."""
    folders = storage.recent(limit)
    if not folders:
        return f"Aucun enregistrement dans {config.RECORDINGS}."
    current = capture.active()
    lines = [f"Dossier : {config.RECORDINGS}"]
    for folder in folders:
        duration = capture.state(folder).get("duration_seconds")
        state = "en cours d'enregistrement" if folder == current else jobs.status(folder)[0].value
        lines.append(
            f"- {folder.name} | {storage.read_meta(folder).get('title', '')} | "
            f"{storage.format_duration(duration) if duration else '-'} | {state}"
        )
    return "\n".join(lines)


@mcp.tool()
def list_audio_sources() -> str:
    """Liste les applications qui utilisent l'audio en ce moment (pour choisir la source),
    le micro et la sortie par défaut."""
    if not capture.is_installed():
        return NOT_INSTALLED
    data = capture.audio_sources()
    shown_apple = ("com.apple.Safari", "com.apple.WebKit.GPU", "com.apple.FaceTime", "com.apple.avconferenced")
    apps = sorted(
        {
            (p["bundle_id"], p["name"], p["playing"])
            for p in data["processes"]
            if p["bundle_id"] and (not p["bundle_id"].startswith("com.apple.") or p["bundle_id"] in shown_apple)
        }
    )
    lines = [f"Micro : {data['default_input']}", f"Sortie : {data['default_output']}", "Applications audio :"]
    lines += [f"- {bundle} ({name or '?'}){' : joue du son' if playing else ''}" for bundle, name, playing in apps]
    lines.append(f"Raccourcis de source : {', '.join(capture.SOURCES)}")
    return "\n".join(lines)


def main() -> None:
    config.extend_path()
    config.RECORDINGS.mkdir(parents=True, exist_ok=True)
    mcp.run()
