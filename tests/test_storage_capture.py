import pytest

from call_recorder_mcp import capture, config, jobs, storage


def test_slug():
    assert storage.slug("Point produit : version 2 !") == "point-produit-version-2"
    assert storage.slug("   ") == ""


def test_format_duration():
    assert storage.format_duration(0) == "00:00:00"
    assert storage.format_duration(3725.9) == "01:02:05"


def test_create_and_resolve():
    first = storage.create("Appel WhatsApp", title="Appel", source="whatsapp")
    assert first.parent == config.RECORDINGS
    assert first.name.endswith("_appel-whatsapp")
    assert storage.read_meta(first)["source"] == "whatsapp"
    assert "started_at" in storage.read_meta(first)
    assert storage.resolve("latest") == first
    assert storage.resolve(first.name) == first
    assert storage.resolve(str(first)) == first


def test_resolve_errors():
    with pytest.raises(ValueError, match="Aucun enregistrement"):
        storage.resolve("latest")
    with pytest.raises(ValueError, match="introuvable"):
        storage.resolve("nope")


def test_update_meta_keeps_other_fields():
    folder = storage.create("x", title="Titre", speakers=1)
    storage.update_meta(folder, speakers=3)
    assert storage.read_meta(folder)["title"] == "Titre"
    assert storage.read_meta(folder)["speakers"] == 3


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("system", ("system", [])),
        ("  Teams ", ("teams", ["com.microsoft.teams"])),
        ("com.example.app", ("com.example.app", ["com.example.app"])),
    ],
)
def test_resolve_source(source, expected):
    assert capture.resolve_source(source) == expected


def test_whatsapp_captures_callkit_audio():
    assert "com.apple.avconferenced" in capture.SOURCES["whatsapp"]


def test_unknown_source_is_refused():
    with pytest.raises(ValueError, match="Source inconnue"):
        capture.resolve_source("myspace")


def test_no_active_recording_without_live_process():
    folder = storage.create("x")
    storage.write_json(folder / "capture.json", {"state": "recording", "pid": 999_999})
    (config.RECORDINGS / ".active").write_text(str(folder))
    assert capture.active() is None


def test_job_status():
    folder = storage.create("x")
    assert jobs.status(folder) == (jobs.Status.NONE, "")
    storage.write_json(folder / jobs.STATUS_FILE, {"state": "running", "pid": 999_999})
    assert jobs.status(folder)[0] is jobs.Status.CRASHED
    storage.write_json(folder / jobs.STATUS_FILE, {"state": "error", "error": "boom"})
    assert jobs.wait(folder, 0) == (jobs.Status.ERROR, "boom")
