import json

import pytest

from call_recorder_mcp import config, storage, transcription
from call_recorder_mcp.spans import Span
from call_recorder_mcp.transcription import Segment


def segment(start, end, text, speaker="Moi"):
    return Segment(start=start, end=end, text=text, speaker=speaker)


@pytest.mark.parametrize(
    "text",
    [
        "Sous-titres réalisés par la communauté d'Amara.org",
        "Sous-titrage ST' 501",
        "Merci.",
        "[Musique]",
        "Vocabulaire : iOS, Android.",
    ],
)
def test_known_hallucinations_are_filtered(text):
    assert transcription.HALLUCINATIONS.search(text)


@pytest.mark.parametrize("text", ["Merci pour ton retour, on avance.", "On publie la version iOS jeudi."])
def test_real_speech_is_kept(text):
    assert not transcription.HALLUCINATIONS.search(text)


def test_vocabulary_prompt_skips_comments_and_blank_lines(tmp_path):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_text("# comment\n\niOS\n  TestFlight  \n")
    assert transcription.vocabulary_prompt(vocabulary) == "Vocabulaire: iOS, TestFlight."


def test_no_vocabulary_means_no_prompt():
    assert transcription.vocabulary_prompt(config.VOCABULARY) == ""


def test_label_numbers_voices_by_first_appearance():
    segments = [(Span(5.0, 6.0, 0), "b"), (Span(1.0, 2.0, 1), "a"), (Span(8.0, 9.0, 1), "c")]
    labelled = transcription.label("remote", segments, speakers=2)
    assert [(s["text"], s["speaker"]) for s in labelled] == [
        ("a", "Interlocuteur 1"),
        ("b", "Interlocuteur 2"),
        ("c", "Interlocuteur 1"),
    ]


def test_label_keeps_single_speaker_names():
    segments = [(Span(1.0, 2.0), "salut")]
    assert transcription.label("mic", segments, speakers=3)[0]["speaker"] == "Moi"
    assert transcription.label("remote", segments, speakers=1)[0]["speaker"] == "Interlocuteur"
    assert transcription.label("audio", segments, speakers=2)[0]["speaker"] == "Personne 1"


def test_drop_bleed_removes_mic_echo_of_the_other_side():
    remote = [segment(10.0, 14.0, "On publie la version jeudi.", "Interlocuteur")]
    mic = [segment(10.2, 14.1, "On publie la version jeudi"), segment(20.0, 22.0, "D'accord, parfait.")]
    assert [s["text"] for s in transcription.drop_bleed(mic, remote)] == ["D'accord, parfait."]


def test_merge_turns_joins_consecutive_segments_of_a_speaker():
    turns = transcription.merge_turns(
        [
            segment(0.0, 2.0, "Bonjour."),
            segment(2.5, 4.0, "Ça va ?"),
            segment(4.5, 6.0, "Oui.", "Interlocuteur"),
            segment(20.0, 21.0, "Super."),
        ]
    )
    assert [(t["speaker"], t["text"]) for t in turns] == [
        ("Moi", "Bonjour. Ça va ?"),
        ("Interlocuteur", "Oui."),
        ("Moi", "Super."),
    ]


def test_render_and_rename_speakers():
    folder = storage.create("Réunion", title="Réunion produit", source="teams", speakers=2)
    turns = [
        segment(0.0, 2.0, "Bonjour à tous.", "Interlocuteur 1"),
        segment(3.0, 4.0, "Salut.", "Interlocuteur 2"),
    ]
    transcription.render(folder, turns, "fr")
    markdown = (folder / "transcript.md").read_text()
    assert markdown.startswith("# Réunion produit\n")
    assert "- Participants : Interlocuteur 1, Interlocuteur 2" in markdown
    assert "**[00:00:00] Interlocuteur 1** : Bonjour à tous." in markdown

    transcription.rename_speakers(folder, {"Interlocuteur 2": "Alice"})
    assert "**[00:00:03] Alice** : Salut." in (folder / "transcript.md").read_text()
    assert json.loads((folder / "transcript.json").read_text())["turns"][1]["speaker"] == "Alice"


def test_rename_unknown_speaker_is_refused():
    folder = storage.create("Appel")
    transcription.render(folder, [segment(0.0, 1.0, "Allô ?", "Interlocuteur")], "fr")
    with pytest.raises(ValueError, match="Participants inconnus"):
        transcription.rename_speakers(folder, {"Interlocuteur 3": "Bob"})
