"""Typed runtime context for command handlers.

``Settings.from_config`` is the canonical config parse: ``apply_config``
builds a Settings and mirrors it onto the ``bambu.<NAME>`` module globals,
which remain only as a write-through compatibility layer for tests and
legacy callers. Handlers take per-call snapshots via
``RuntimeContext.from_globals(args)`` / ``Settings.from_globals()`` so test
patches on the globals are still honored. Do not add new module globals;
add fields here instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    Field defaults mirror the current module-global defaults in
    ``bambu_cli/bambu.py``.
    """

    printer_ip: str = "0.0.0.0"
    serial: str = "UNKNOWN"
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
        camera_stream_url = cfg.get(
            "camera_stream_url", f"http://localhost:{host_port}/api/frame.jpeg?src=p1s"
        )

        return cls(
            printer_ip=cfg.get("printer_ip", "0.0.0.0"),
            serial=cfg.get("serial", "UNKNOWN"),
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
            allow_private_ips=False,
        )

    @classmethod
    def from_globals(cls) -> Settings:
        """Snapshot the current ``bambu.<NAME>`` module globals."""
        from bambu_cli import bambu

        return cls(
            printer_ip=bambu.PRINTER_IP,
            serial=bambu.SERIAL,
            mqtt_port=bambu.MQTT_PORT,
            insecure_tls=bambu.INSECURE_TLS,
            cert_fingerprint=getattr(bambu, "_cfg", {}).get("cert_fingerprint") if getattr(bambu, "_cfg", None) else None,
            orca_slicer=bambu.ORCA_SLICER,
            profiles_dir=bambu.PROFILES_DIR,
            printer_model=bambu.PRINTER_MODEL,
            nozzle_size=bambu.NOZZLE_SIZE,
            camera_image=bambu.CAMERA_IMAGE,
            camera_container_name=bambu.CAMERA_CONTAINER_NAME,
            camera_port=bambu.CAMERA_PORT,
            camera_stream_url=bambu.CAMERA_STREAM_URL,
            allow_private_ips=getattr(bambu, "ALLOW_PRIVATE_IPS", False),
        )


@dataclass
class RuntimeContext:
    """Typed bundle of the state a command needs to run.

    Compat note: the module globals on ``bambu_cli.bambu`` remain the source
    of truth for now; this context is a snapshot/cache layer alongside them.
    """

    settings: Settings = field(default_factory=Settings)
    simulation: bool = False
    json_mode: bool = False
    config_path: Path | None = None
    last_error: dict | None = None
    _printer: Any = field(default=None, repr=False, compare=False)

    def printer(self):
        """Return a cached ``BambuPrinter`` built from ``self.settings``.

        Mirrors ``bambu_cli.printer.get_printer()``: empty access_code in
        simulation mode, otherwise loaded via ``load_access_code()``.
        """
        if self._printer is not None:
            return self._printer

        from bambu_cli import bambu
        from bambu_cli.printer import BambuPrinter

        access_code = "" if self.simulation else bambu.load_access_code()
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
    def from_globals(cls, args=None) -> RuntimeContext:
        """Snapshot the current globals into a RuntimeContext."""
        from bambu_cli import bambu

        json_mode = False
        if args is not None:
            from bambu_cli.cli import _json_mode_requested

            json_mode = _json_mode_requested(args)

        return cls(
            settings=Settings.from_globals(),
            simulation=bool(getattr(bambu, "SIMULATION_MODE", False)),
            json_mode=json_mode,
        )


_current: RuntimeContext | None = None


def get_current() -> RuntimeContext:
    """Return the current RuntimeContext, lazily building one from globals
    if none has been set yet (so library/test callers that never ran
    ``main()`` still work).
    """
    global _current
    if _current is None:
        _current = RuntimeContext.from_globals()
    return _current


def set_current(ctx: RuntimeContext) -> None:
    """Set the process-wide current RuntimeContext."""
    global _current
    _current = ctx
