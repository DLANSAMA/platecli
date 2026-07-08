from unittest.mock import patch

import bambu_cli.bambu as bambu
from bambu_cli import context


def test_settings_from_config_maps_all_keys():
    cfg = {
        "printer_ip": "10.0.0.5",
        "serial": "SN123",
        "mqtt_port": 1234,
        "insecure_tls": True,
        "cert_fingerprint": "AA:BB:CC",
        "orca_slicer": "/tmp/orca",
        "profiles_dir": "/tmp/profiles",
        "model": "x1c",
        "nozzle": "0.6",
        "camera_image": "custom_image",
        "camera_container_name": "custom_container",
        "camera_port": "9999:9998",
        "camera_stream_url": "http://example/frame.jpeg",
    }
    settings = context.Settings.from_config(cfg)
    assert settings.printer_ip == "10.0.0.5"
    assert settings.serial == "SN123"
    assert settings.mqtt_port == 1234
    assert settings.insecure_tls is True
    assert settings.cert_fingerprint == "AA:BB:CC"
    assert settings.orca_slicer == "/tmp/orca"
    assert settings.profiles_dir == "/tmp/profiles"
    assert settings.printer_model == "X1C"
    assert settings.nozzle_size == "0.6"
    assert settings.camera_image == "custom_image"
    assert settings.camera_container_name == "custom_container"
    assert settings.camera_port == "9999:9998"
    assert settings.camera_stream_url == "http://example/frame.jpeg"
    assert settings.allow_private_ips is False


def test_settings_from_config_defaults_for_missing_keys():
    settings = context.Settings.from_config({})
    assert settings.printer_ip == "0.0.0.0"
    assert settings.serial == "UNKNOWN"
    assert settings.mqtt_port == 8883
    assert settings.insecure_tls is False
    assert settings.cert_fingerprint is None
    assert settings.printer_model == "P1P"
    assert settings.nozzle_size == "0.4"
    assert settings.camera_image == "bambu_p1_streamer"
    assert settings.camera_container_name == "bambu_camera"
    assert settings.camera_port == "1985:1984"
    assert settings.camera_stream_url == "http://localhost:1985/api/frame.jpeg?src=p1s"


def test_settings_from_config_alt_keys_and_none():
    settings = context.Settings.from_config(None)
    assert settings.printer_ip == "0.0.0.0"

    settings = context.Settings.from_config({"printer_model": "a1", "nozzle_size": "0.2"})
    assert settings.printer_model == "A1"
    assert settings.nozzle_size == "0.2"


def test_runtime_context_printer_simulation_mode():
    settings = context.Settings(printer_ip="1.2.3.4", serial="SN1", mqtt_port=8883,
                                 insecure_tls=False, cert_fingerprint=None)
    ctx = context.RuntimeContext(settings=settings, simulation=True)
    printer = ctx.printer()
    assert printer.ip == "1.2.3.4"
    assert printer.serial == "SN1"
    assert printer.access_code == ""
    assert printer.simulation_mode is True
    # cached
    assert ctx.printer() is printer


def test_runtime_context_printer_non_simulation_uses_load_access_code():
    settings = context.Settings(printer_ip="1.2.3.4", serial="SN1",
                                 cert_fingerprint="AA:BB")
    ctx = context.RuntimeContext(settings=settings, simulation=False)
    with patch.object(bambu, "load_access_code", return_value="secretcode") as mock_load:
        printer = ctx.printer()
    mock_load.assert_called_once()
    assert printer.access_code == "secretcode"
    assert printer.cert_fingerprint == "aabb"


def test_get_current_lazy_builds_and_set_current_overrides():
    context.set_current(None)
    ctx = context.get_current()
    assert isinstance(ctx, context.RuntimeContext)

    custom = context.RuntimeContext(settings=context.Settings(serial="CUSTOM"))
    context.set_current(custom)
    assert context.get_current() is custom
    # reset so other tests aren't polluted
    context.set_current(None)


@patch('sys.argv', ['bambu.py', '--sim', 'status'])
@patch('bambu_cli.bambu.cmd_status')
@patch('bambu_cli.cli.setup_logging')
def test_main_populates_current_context(mock_setup_logging, mock_cmd_status):
    context.set_current(None)
    bambu.main()
    ctx = context.get_current()
    assert ctx.simulation is True
    context.set_current(None)
