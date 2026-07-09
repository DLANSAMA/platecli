from __future__ import annotations

"""Typed runtime context for command handlers.

``Settings.from_config`` is the canonical config parse: ``apply_config``
builds a ``RuntimeContext`` and installs it via ``set_current`` as the single
source of truth. Handlers read it through ``current_settings()`` /
``current_config()`` / ``current_simulation()``. There are no config-derived
module globals anymore — add fields to ``Settings`` here instead.
"""


from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bambu_cli.printer import BambuPrinter


def _normalize_fingerprint(fp: str | None) -> str | None:
    """Normalize a pinned SHA-256 fingerprint (lowercase, separator-free).

    Mirrors ``bambu_cli.config._expected_fingerprint``.
    """
    if not fp:
        return None
    return fp.lower().replace(":", "").replace(" ", "")


@dataclass
class Settings:
    """Typed snapshot of the printer/runtime configuration.

    Field defaults are the values an empty config resolves to.
    """

    printer_ip: str = "0.0.0.0"
    serial: str = "UNKNOWN"
    username: str = "bblp"
    mqtt_port: int = 8883
    insecure_tls: bool = False
    cert_fingerprint: str | None = None
    orca_slicer: str = ""
    profiles_dir: str = ""
    printer_model: str = "P1P"
    nozzle_size: str = "0.4"
    camera_image: str = "bambu_p1_streamer"
    camera_container_name: str = "bambu_camera"
    camera_port: str = "1985:1984"
    camera_stream_url: str = ""
    allow_private_ips: bool = False

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> Settings:
        """Build a Settings from a config dict, using the same key names and
        parsing that ``bambu_cli.config.apply_config`` uses.
        """
        from bambu_cli.cli import _expand_path

        cfg = cfg or {}

        default_orca = ""
        default_profiles = ""
        try:
            from bambu_cli import config as _config_mod

            default_orca = _config_mod._DEFAULT_ORCA or ""
            default_profiles = _config_mod._DEFAULT_PROFILES or ""
        except Exception:
            pass

        orca_slicer = _expand_path(cfg.get("orca_slicer", default_orca))
        profiles_dir = _expand_path(cfg.get("profiles_dir", default_profiles))
        printer_model = cfg.get("model", cfg.get("printer_model", "P1P")).upper()
        nozzle_size = str(cfg.get("nozzle", cfg.get("nozzle_size", "0.4")))
        camera_port = cfg.get("camera_port", "1985:1984")
        host_port = camera_port.split(":")[0]
        camera_stream_url = cfg.get("camera_stream_url", f"http://localhost:{host_port}/api/frame.jpeg?src=p1s")

        return cls(
            printer_ip=cfg.get("printer_ip", "0.0.0.0"),
            serial=cfg.get("serial", "UNKNOWN"),
            username=cfg.get("username", "bblp"),
            mqtt_port=cfg.get("mqtt_port", 8883),
            insecure_tls=cfg.get("insecure_tls", False),
            cert_fingerprint=cfg.get("cert_fingerprint"),
            orca_slicer=orca_slicer,
            profiles_dir=profiles_dir,
            printer_model=printer_model,
            nozzle_size=nozzle_size,
            camera_image=cfg.get("camera_image", "bambu_p1_streamer"),
            camera_container_name=cfg.get("camera_container_name", "bambu_camera"),
            camera_port=camera_port,
            camera_stream_url=camera_stream_url,
            # Always false from config: private-IP downloads are a per-invocation
            # CLI override only (``--allow-private-ips``), never a sticky config key.
            allow_private_ips=False,
        )


@dataclass
class RuntimeContext:
    """Typed bundle of the state a command needs to run.

    Installed via ``set_current`` (by ``main`` and by tests) and read back
    through the ``current_*`` accessors below; it is the source of truth for
    per-run configuration.
    """

    settings: Settings = field(default_factory=Settings)
    config: dict[str, Any] = field(default_factory=dict)
    simulation: bool = False
    json_mode: bool = False
    config_path: Path | None = None
    last_error: dict | None = None
    _printer: Any = field(default=None, repr=False, compare=False)

    def printer(self) -> BambuPrinter:
        """Return a cached ``BambuPrinter`` built from ``self.settings``.

        Mirrors ``bambu_cli.printer.get_printer()``: empty access_code in
        simulation mode, otherwise loaded via ``load_access_code()``.
        """
        if self._printer is not None:
            return self._printer

        from bambu_cli.config import load_access_code
        from bambu_cli.printer import BambuPrinter

        access_code = "" if self.simulation else load_access_code()
        self._printer = BambuPrinter(
            ip=self.settings.printer_ip,
            serial=self.settings.serial,
            access_code=access_code,
            insecure_tls=self.settings.insecure_tls,
            cert_fingerprint=_normalize_fingerprint(self.settings.cert_fingerprint),
            simulation_mode=self.simulation,
        )
        return self._printer

    @classmethod
    def for_request(cls, args: Any = None) -> RuntimeContext:
        """Return the installed RuntimeContext for the current command.

        ``json_mode`` is refreshed from ``args`` when provided. Handlers use the
        ``ctx = ctx or RuntimeContext.for_request(args)`` idiom so they work both
        under ``main`` (which installs the context) and when called directly.
        """
        ctx = get_current()
        if args is not None:
            from bambu_cli.cli import _json_mode_requested

            ctx.json_mode = _json_mode_requested(args)
        return ctx


_current: RuntimeContext | None = None


def get_current() -> RuntimeContext:
    """Return the active RuntimeContext, installing a default one if none has
    been set yet (so library/test callers that never ran ``main()`` still get
    a usable, mutable context).
    """
    global _current
    if _current is None:
        _current = RuntimeContext()
    return _current


def set_current(ctx: RuntimeContext | None) -> None:
    """Set (or, with ``None``, clear) the process-wide current RuntimeContext."""
    global _current
    _current = ctx


# --- Runtime config accessors ------------------------------------------------
# The single funnel through which handlers read runtime config, backed by the
# installed RuntimeContext (see get_current).


def current_settings() -> Settings:
    """Typed settings for the active run."""
    return get_current().settings


def current_config() -> dict[str, Any]:
    """Raw config dict for the active run."""
    return dict(get_current().config)


def current_simulation() -> bool:
    """Whether the active run is in simulation mode."""
    return get_current().simulation
