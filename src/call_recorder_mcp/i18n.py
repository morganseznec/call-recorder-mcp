"""User-facing text in 20 languages (one JSON catalog per language in locales/).

The language is CALL_RECORDER_LOCALE when set ("fr", "pt-PT", "zh_TW"...), else
the first macOS preferred language, else English. Transcription defaults to the
same language (CALL_RECORDER_LANGUAGE overrides it).
"""

import functools
import json
import re
import subprocess
from importlib import resources

from . import config

# Same set as the author's iOS apps.
LOCALES = (
    "ar", "de", "en", "es", "fr", "hi", "id", "it", "ja", "ko",
    "nl", "pt-BR", "pt-PT", "ru", "sw", "th", "tr", "vi", "zh-Hans", "zh-Hant",
)  # fmt: skip
FALLBACK = "en"
TRADITIONAL_CHINESE_REGIONS = ("tw", "hk", "mo")


@functools.cache
def catalog(locale: str) -> dict[str, str]:
    return json.loads((resources.files(__package__) / "locales" / f"{locale}.json").read_text(encoding="utf-8"))


def normalize(tag: str) -> str:
    """BCP 47 or POSIX tag ("fr-FR", "zh_TW", "zh-Hant-HK", "pt") -> one of LOCALES."""
    parts = tag.strip().replace("_", "-").lower().split("-")
    base = parts[0]
    if base == "zh":
        traditional = "hant" in parts or any(region in parts for region in TRADITIONAL_CHINESE_REGIONS)
        return "zh-Hant" if traditional else "zh-Hans"
    if base == "pt":
        return "pt-PT" if "pt" in parts[1:] else "pt-BR"
    return base if base in LOCALES else FALLBACK


@functools.cache
def _macos_language() -> str:
    """First of the user's preferred languages (System Settings > General > Language & Region)."""
    try:
        output = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*", output)
    return match.group(0) if match else ""


def locale() -> str:
    """One of LOCALES."""
    tag = config.LOCALE or _macos_language()
    return normalize(tag) if tag else FALLBACK


def language() -> str:
    """Default transcription language, as a Whisper code ("zh-Hant" -> "zh")."""
    return config.LANGUAGE or locale().split("-")[0]


def t(key: str, **values: object) -> str:
    return catalog(locale())[key].format(**values)


def all_translations(key: str) -> set[str]:
    """A message in every language (e.g. to recognize the glossary prompt Whisper echoes)."""
    return {catalog(code)[key] for code in LOCALES}
