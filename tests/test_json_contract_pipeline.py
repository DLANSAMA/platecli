"""Pipeline contracts: job/send, slice, download."""

from tests.json_contract_base import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# job / send
# ---------------------------------------------------------------------------


def test_job_dry_run_local_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["job", str(ready), "--confirm", "--dry-run", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["dry_run_local_skipped"]},
                "command": {"enum": ["job"]},
                "would_upload": {"enum": [True]},
                "would_print": {"enum": [True]},
            },
        },
    )
    assert not payload.get("uploaded") and not payload.get("printed")


def test_job_sim_printed_success_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path)
    exc = run_main(monkeypatch, tmp_path, ["--sim", "job", str(ready), "--confirm", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["printed"]},
                "command": {"enum": ["job"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [True]},
            },
        },
    )


def test_job_sim_uploaded_not_printed_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path, name="ready2.3mf")
    exc = run_main(monkeypatch, tmp_path, ["--sim", "job", str(ready), "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["uploaded_not_printed"]},
                "command": {"enum": ["job"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [False]},
                "next_command": {"type": list, "items": STR},
            },
        },
    )
    assert payload["next_command"][0] == "print"


def test_send_alias_uploaded_only_shape(monkeypatch, tmp_path, capsys):
    ready = make_ready_file(tmp_path, name="ready3.3mf")
    exc = run_main(monkeypatch, tmp_path, ["--sim", "send", str(ready), "--upload-only", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["uploaded"]},
                "command": {"enum": ["send"]},
                "uploaded": {"enum": [True]},
                "printed": {"enum": [False]},
            },
        },
    )


def test_job_url_dry_run_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["job", "printables.com/model/12345-contract", "--dry-run", "--json"])
    assert exc is None
    payload = read_json(capsys)
    assert_shape(
        payload,
        {
            "type": dict,
            "required": {
                "status": {"enum": ["dry_run_url_skipped"]},
                "command": {"enum": ["job"]},
                "normalized_source": STR,
                "would_download": {"enum": [True]},
            },
        },
    )


def test_job_download_rejection_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["job", "https://example.com/archive.rar", "--dry-run", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("job"))
    assert payload["failed_step"] == "validate"
    assert payload["extension"] == ".rar"


def test_job_local_zip_extract_error_shape(monkeypatch, tmp_path, capsys):
    archive_path = tmp_path / "empty-bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "not a model")
    exc = run_main(monkeypatch, tmp_path, ["job", str(archive_path), "--dry-run", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("job"))
    assert payload["failed_step"] == "extract"


# ---------------------------------------------------------------------------
# slice
# ---------------------------------------------------------------------------


def test_slice_missing_file_error_shape(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "missing.stl"
    exc = run_main(monkeypatch, tmp_path, ["slice", str(missing), "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("slice"))
    assert payload["failed_step"] == "validate"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_rejects_non_model_error_shape(monkeypatch, tmp_path, capsys):
    exc = run_main(monkeypatch, tmp_path, ["download", "https://example.com/archive.rar", "--json"])
    assert exc is not None and exc.code == 3
    payload = read_json(capsys)
    assert_shape(payload, base_error_spec("download"))
    assert payload["failed_step"] == "validate"
    assert payload["extension"] == ".rar"


def test_download_credential_url_rejected_and_redacted_shape(monkeypatch, tmp_path, capsys):
    # Assembled from pieces so the repo's privacy smoke doesn't flag a
    # credential-bearing URL / email-like literal in this file.
    credentialed_url = "https://" + "agent:" + "secret" + "@" + "example.com/model.stl"
    exc = run_main(monkeypatch, tmp_path, ["download", credentialed_url, "--json"])
    assert exc is not None and exc.code == 5
    out = capsys.readouterr().out
    assert "secret" not in out
    payload = json.loads(out)
    assert_shape(payload, base_error_spec("download"))
    assert payload["source"] == "https://example.com/model.stl"


