import json

from jobs import STATE_FILENAME, JobState


def test_load_missing_file_gives_empty_state(tmp_path):
    state = JobState.load(tmp_path)
    assert len(state) == 0
    assert state.get("cualquiera.nc") is None


def test_record_and_reload_roundtrip(tmp_path):
    state = JobState.load(tmp_path)
    state.record("era5_1996_1998_all.nc", "req-123", "reanalysis-era5-single-levels")
    state.save()

    reloaded = JobState.load(tmp_path)
    assert reloaded.get("era5_1996_1998_all.nc") == "req-123"
    assert reloaded.pending() == ["era5_1996_1998_all.nc"]


def test_forget_removes_entry(tmp_path):
    state = JobState.load(tmp_path)
    state.record("a.nc", "req-1", "ds")
    state.forget("a.nc")
    state.save()

    assert JobState.load(tmp_path).get("a.nc") is None


def test_forget_unknown_target_is_noop(tmp_path):
    state = JobState.load(tmp_path)
    state.forget("no-existe.nc")  # no debe lanzar
    assert len(state) == 0


def test_corrupt_state_file_is_discarded(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("{ esto no es json", encoding="utf-8")

    state = JobState.load(tmp_path)
    assert len(state) == 0


def test_save_does_not_leave_temp_file(tmp_path):
    state = JobState.load(tmp_path)
    state.record("a.nc", "req-1", "ds")
    state.save()

    names = {p.name for p in tmp_path.iterdir()}
    assert names == {STATE_FILENAME}


def test_saved_file_is_valid_json(tmp_path):
    state = JobState.load(tmp_path)
    state.record("a.nc", "req-1", "reanalysis-era5-single-levels")
    state.save()

    payload = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
    assert payload["a.nc"]["request_id"] == "req-1"
    assert payload["a.nc"]["dataset"] == "reanalysis-era5-single-levels"
    assert "submitted_at" in payload["a.nc"]
