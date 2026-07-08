import json
from unittest.mock import patch

import pytest

from bambu_cli.errors import (
    BambuError,
    ConfigError,
    PrinterConnectionError,
    AuthError,
    UploadError,
    DownloadRejected,
    SliceError,
    abort,
    NetworkError,
    FileError,
)
from bambu_cli.constants import EXIT_CONFIG_ERROR, EXIT_COMMAND_ERROR, EXIT_NETWORK_ERROR, EXIT_FILE_ERROR
import bambu_cli.bambu as bambu


def test_base_defaults():
    exc = BambuError("boom")
    assert exc.exit_code == EXIT_COMMAND_ERROR
    assert exc.failed_step is None
    assert exc.detail == {}
    assert exc.next_command is None
    assert str(exc) == "boom"


def test_overrides():
    exc = BambuError(
        "boom",
        detail={"a": 1},
        next_command="bambu setup",
        exit_code=42,
        failed_step="custom",
    )
    assert exc.exit_code == 42
    assert exc.failed_step == "custom"
    assert exc.detail == {"a": 1}
    assert exc.next_command == "bambu setup"


def test_config_error_defaults():
    exc = ConfigError("no config")
    assert exc.exit_code == EXIT_CONFIG_ERROR
    assert exc.failed_step == "config"


def test_printer_connection_error_defaults():
    exc = PrinterConnectionError("cannot connect")
    assert exc.failed_step == "connect"
    assert exc.exit_code == EXIT_COMMAND_ERROR


def test_auth_error_is_connection_error():
    exc = AuthError("bad access code")
    assert isinstance(exc, PrinterConnectionError)
    assert exc.failed_step == "connect"


def test_upload_error_defaults():
    assert UploadError("x").failed_step == "upload"


def test_download_rejected_defaults():
    assert DownloadRejected("x").failed_step == "download"


def test_slice_error_defaults():
    assert SliceError("x").failed_step == "slice"


def test_abort_picks_network_error_class():
    with pytest.raises(NetworkError) as ei:
        abort("net", exit_code=EXIT_NETWORK_ERROR)
    assert ei.value.exit_code == EXIT_NETWORK_ERROR


def test_abort_picks_file_error_class():
    with pytest.raises(FileError) as ei:
        abort("file", exit_code=EXIT_FILE_ERROR)
    assert ei.value.exit_code == EXIT_FILE_ERROR


def test_exported_from_bambu_facade():
    assert bambu.BambuError is BambuError
    assert bambu.ConfigError is ConfigError
    assert bambu.UploadError is UploadError


def test_main_converts_bambu_error_to_json_payload(capsys):
    def raise_it(args):
        raise UploadError(
            "upload failed",
            detail={"file": "a.3mf"},
            next_command="bambu upload a.3mf",
        )

    with patch("sys.argv", ["bambu.py", "status", "--json"]), \
            patch("bambu_cli.cli.setup_logging"), \
            patch("socket.getaddrinfo", return_value=[]), \
            patch("bambu_cli.bambu.cmd_status", side_effect=raise_it, create=True):
        with pytest.raises(SystemExit) as excinfo:
            bambu.main()

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_COMMAND_ERROR
    output = capsys.readouterr().out
    payload = json.loads(output.strip())
    assert payload["status"] == "error"
    assert payload["failed_step"] == "upload"
    assert payload["exit_code"] == EXIT_COMMAND_ERROR
    assert payload["detail"] == {"file": "a.3mf"}
    assert payload["next_command"] == "bambu upload a.3mf"


def test_main_non_json_mode_exits_with_code():
    def raise_it(args):
        raise ConfigError("no config found")

    with patch("sys.argv", ["bambu.py", "status"]), \
            patch("bambu_cli.cli.setup_logging"), \
            patch("socket.getaddrinfo", return_value=[]), \
            patch("bambu_cli.bambu.cmd_status", side_effect=raise_it, create=True):
        with pytest.raises(SystemExit) as excinfo:
            bambu.main()

    assert getattr(excinfo.value, "exit_code", getattr(excinfo.value, "code", None)) == EXIT_CONFIG_ERROR
