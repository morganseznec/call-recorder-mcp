import string
from importlib import resources

import pytest

from call_recorder_mcp import config, i18n, storage, transcription
from call_recorder_mcp.spans import Span


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("fr", "fr"),
        ("fr-FR", "fr"),
        ("fr_CA", "fr"),
        ("en-GB", "en"),
        ("de-CH", "de"),
        ("ja-JP", "ja"),
        ("ar", "ar"),
        ("zh-Hans-CN", "zh-Hans"),
        ("zh_CN", "zh-Hans"),
        ("zh-Hant-TW", "zh-Hant"),
        ("zh_TW", "zh-Hant"),
        ("zh-HK", "zh-Hant"),
        ("pt", "pt-BR"),
        ("pt-BR", "pt-BR"),
        ("pt_PT", "pt-PT"),
        ("pl-PL", "en"),
    ],
)
def test_normalize(tag, expected):
    assert i18n.normalize(tag) == expected


def test_locale_setting_wins_over_macos(monkeypatch):
    monkeypatch.setattr(i18n, "_macos_language", lambda: "ja-JP")
    monkeypatch.setattr(config, "LOCALE", "pt_PT")
    assert i18n.locale() == "pt-PT"


def test_locale_follows_macos_when_not_set(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "")
    monkeypatch.setattr(i18n, "_macos_language", lambda: "zh-Hant-TW")
    assert i18n.locale() == "zh-Hant"
    monkeypatch.setattr(i18n, "_macos_language", lambda: "")
    assert i18n.locale() == "en"


def test_transcription_language_follows_the_interface(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "zh-Hant")
    assert i18n.language() == "zh"
    monkeypatch.setattr(config, "LOCALE", "pt-PT")
    assert i18n.language() == "pt"
    monkeypatch.setattr(config, "LANGUAGE", "auto")
    assert i18n.language() == "auto"


def _placeholders(text):
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name)


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_catalog_matches_english(locale):
    english, catalog = i18n.catalog("en"), i18n.catalog(locale)
    assert list(catalog) == list(english)
    for key, text in english.items():
        assert _placeholders(catalog[key]) == _placeholders(text), key
        assert catalog[key].strip(), key
    # Appended after a sentence: a space, except after the CJK full stop "。".
    assert catalog["notes"].startswith(" ") != (locale in ("ja", "zh-Hans", "zh-Hant"))


@pytest.mark.parametrize("locale", [code for code in i18n.LOCALES if code != "en"])
def test_permission_prompts_are_translated(locale):
    strings = resources.files("call_recorder_mcp") / "macos" / f"{locale}.lproj" / "InfoPlist.strings"
    text = strings.read_text(encoding="utf-8")
    assert '"NSMicrophoneUsageDescription"' in text
    assert '"NSAudioCaptureUsageDescription"' in text


def test_echoed_glossary_prefix_is_filtered_in_every_language():
    for word in i18n.all_translations("glossary"):
        assert transcription.is_hallucination(f"{word}: iOS, TestFlight.")


def test_english_labels_and_transcript(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    segments = [(Span(0.0, 1.0, 0), "hi"), (Span(2.0, 3.0, 1), "hello")]
    assert transcription.label("mic", segments[:1], speakers=1)[0]["speaker"] == "Me"
    assert transcription.label("remote", segments[:1], speakers=1)[0]["speaker"] == "Other side"
    assert [s["speaker"] for s in transcription.label("remote", segments, speakers=2)] == ["Speaker 1", "Speaker 2"]

    folder = storage.create("call", title="Weekly sync", source="teams")
    transcription.render(folder, transcription.label("remote", segments, speakers=2), "en")
    markdown = (folder / "transcript.md").read_text()
    assert "- Participants: Speaker 1, Speaker 2" in markdown
    assert "## Transcript" in markdown
    assert "**[00:00:02] Speaker 2**: hello" in markdown


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_every_language_renders_a_transcript(monkeypatch, locale):
    monkeypatch.setattr(config, "LOCALE", locale)
    folder = storage.create("call", title="Sync", source="teams")
    segments = transcription.label("remote", [(Span(0.0, 1.0, 0), "a"), (Span(2.0, 3.0, 1), "b")], speakers=2)
    transcription.render(folder, segments, i18n.language())
    markdown = (folder / "transcript.md").read_text(encoding="utf-8")
    assert f"## {i18n.t('transcript')}" in markdown
    assert i18n.t("remote_n", n=2) in markdown
