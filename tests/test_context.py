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


def test_stream_host_port_derivation():
    # Container port is always the last colon field; host port the one before it,
    # across every docker -p form (including bracketed IPv6). Non-derivable forms
    # (bare container port, empty host port, ranges) fall back to the default.
    d = context._stream_host_port
    assert d("1985:1984") == "1985"
    assert d("127.0.0.1:1985:1984") == "1985"
    assert d("0.0.0.0:1985:1984") == "1985"
    assert d("[::1]:1985:1984") == "1985"
    assert d("1985:1984/tcp") == "1985"
    assert d("1984") == "1985"  # bare container port -> random host port -> fallback
    assert d("127.0.0.1::1984") == "1985"  # empty host port -> random -> fallback
    assert d("1985-1990:1984-1989") == "1985"  # range -> fallback


def test_settings_from_config_host_qualified_camera_port_url():
    # Regression: a host-qualified port spec must still derive the correct host
    # port for the localhost stream URL (previously took the IP field).
    settings = context.Settings.from_config({"camera_port": "127.0.0.1:1985:1984"})
    assert settings.camera_stream_url == "http://localhost:1985/api/frame.jpeg?src=p1s"


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
    assert settings.camera_port == "127.0.0.1:1985:1984"
    assert settings.camera_stream_url == "http://localhost:1985/api/frame.jpeg?src=p1s"
    assert settings.camera_direct_only is False
    assert settings.camera_allow_streamer is False


def test_settings_from_config_camera_direct_only_is_a_sticky_config_key():
    """camera_direct_only must actually be READ from config.

    Regression guard: ``allow_private_ips`` is deliberately forced to False and
    ignores config (a per-invocation CLI override only). Copying that pattern here
    would produce a security opt-in that silently does nothing while the user
    believes the streamer fallback is closed.
    """
    assert context.Settings.from_config({"camera_direct_only": True}).camera_direct_only is True
    assert context.Settings.from_config({"camera_direct_only": False}).camera_direct_only is False
    # Coerced, so a JSON string cannot arrive as a non-bool.
    assert context.Settings.from_config({"camera_direct_only": "yes"}).camera_direct_only is True
    assert context.Settings.from_config({"camera_allow_streamer": True}).camera_allow_streamer is True
    assert context.Settings.from_config({}).camera_allow_streamer is False
    # Contrast with the forced-False key, which must keep ignoring config.
    assert context.Settings.from_config({"allow_private_ips": True}).allow_private_ips is False


def test_settings_from_config_alt_keys_and_none():
    settings = context.Settings.from_config(None)
    assert settings.printer_ip == "0.0.0.0"

    settings = context.Settings.from_config({"printer_model": "a1", "nozzle_size": "0.2"})
    assert settings.printer_model == "A1"
    assert settings.nozzle_size == "0.2"


def test_runtime_context_printer_simulation_mode():
    settings = context.Settings(
        printer_ip="1.2.3.4", serial="SN1", mqtt_port=8883, insecure_tls=False, cert_fingerprint=None
    )
    ctx = context.RuntimeContext(settings=settings, simulation=True)
    printer = ctx.printer()
    assert printer.ip == "1.2.3.4"
    assert printer.serial == "SN1"
    assert printer.access_code == ""
    assert printer.simulation_mode is True
    assert printer.mqtt_port == 8883
    # cached
    assert ctx.printer() is printer


def test_runtime_context_printer_non_simulation_uses_load_access_code():
    settings = context.Settings(printer_ip="1.2.3.4", serial="SN1", cert_fingerprint="AA:BB")
    ctx = context.RuntimeContext(settings=settings, simulation=False)
    with patch("bambu_cli.config.load_access_code", return_value="secretcode") as mock_load:
        printer = ctx.printer()
    mock_load.assert_called_once()
    assert printer.access_code == "secretcode"
    assert printer.cert_fingerprint == "aabb"
    assert printer.mqtt_port == 8883


def test_runtime_context_printer_honors_configured_mqtt_port():
    settings = context.Settings(printer_ip="1.2.3.4", serial="SN1", mqtt_port=1883)
    ctx = context.RuntimeContext(settings=settings, simulation=True)
    assert ctx.printer().mqtt_port == 1883


def test_runtime_context_printer_uses_installed_factory():
    """RuntimeContext.printer() must go through set_printer_factory, not import printer."""
    import bambu_cli.printer  # noqa: F401 — register the default factory

    previous = context.get_printer_factory()
    sentinel = object()
    seen: list[object] = []

    def factory(ctx):
        seen.append(ctx)
        return sentinel

    try:
        context.set_printer_factory(factory)
        ctx = context.RuntimeContext()
        assert ctx.printer() is sentinel
        assert ctx.printer() is sentinel  # cached
        assert seen == [ctx]
    finally:
        context.set_printer_factory(previous)


def test_runtime_context_printer_requires_factory():
    previous = context.get_printer_factory()
    try:
        context.set_printer_factory(None)
        ctx = context.RuntimeContext()
        try:
            ctx.printer()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "factory" in str(exc).lower()
    finally:
        context.set_printer_factory(previous)


def test_get_current_lazy_builds_and_set_current_overrides():
    context.set_current(None)
    ctx = context.get_current()
    assert isinstance(ctx, context.RuntimeContext)

    custom = context.RuntimeContext(settings=context.Settings(serial="CUSTOM"))
    context.set_current(custom)
    assert context.get_current() is custom
    # reset so other tests aren't polluted
    context.set_current(None)


@patch("sys.argv", ["bambu.py", "--sim", "status"])
@patch("bambu_cli.commands.cmd_status")
@patch("bambu_cli.cli.setup_logging")
def test_main_populates_current_context(mock_setup_logging, mock_cmd_status):
    context.set_current(None)
    __import__("bambu_cli.cli", fromlist=["main"]).main()
    ctx = context.get_current()
    assert ctx.simulation is True
    context.set_current(None)
