import pytest

from call_recorder_mcp import config, i18n, storage, transcription
from call_recorder_mcp.spans import Span


@pytest.mark.parametrize(("setting", "expected"), [("fr", "fr"), ("fr_FR", "fr"), ("en_GB", "en"), ("de_DE", "en")])
def test_locale_setting(monkeypatch, setting, expected):
    monkeypatch.setattr(config, "LOCALE", setting)
    assert i18n.locale() == expected


def test_locale_follows_macos_when_not_set(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "")
    monkeypatch.setattr(i18n, "_macos_locale", lambda: "fr_CA")
    assert i18n.locale() == "fr"


def test_transcription_language_follows_the_interface(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    assert i18n.language() == "en"
    monkeypatch.setattr(config, "LANGUAGE", "auto")
    assert i18n.language() == "auto"


def test_every_message_exists_in_both_languages():
    english, french = i18n.MESSAGES["en"], i18n.MESSAGES["fr"]
    assert english.keys() == french.keys()
    for key in english:
        assert set(_placeholders(english[key])) == set(_placeholders(french[key])), key


def _placeholders(text):
    import string

    return [name for _, name, _, _ in string.Formatter().parse(text) if name]


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
