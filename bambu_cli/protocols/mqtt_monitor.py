"""MQTT live status monitor (human progress bar or NDJSON events)."""

from __future__ import annotations

import json
import sys
import threading

from bambu_cli.contracts import StatusEvent
from bambu_cli.logging_utils import logger
from bambu_cli.protocols.mqtt_cmd import (
    TERMINAL_GCODE_STATES,
    _client_factory,
    _connect,
    _sleep,
)
from bambu_cli.utils import get_sequence_id


def _status_event(p, event):
    """Build a compact, agent-friendly status event from a raw MQTT print payload."""

    def _int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return StatusEvent(
        event=event,
        command="status",
        gcode_state=p.get("gcode_state", "UNKNOWN"),
        mc_percent=_int(p.get("mc_percent", 0)),
    ).to_payload(
        layer_num=_int(p.get("layer_num", 0)),
        total_layer_num=_int(p.get("total_layer_num", 0)),
        mc_remaining_time=_int(p.get("mc_remaining_time", 0)),
        nozzle_temper=p.get("nozzle_temper"),
        nozzle_target_temper=p.get("nozzle_target_temper"),
        bed_temper=p.get("bed_temper"),
        bed_target_temper=p.get("bed_target_temper"),
        gcode_file=p.get("gcode_file", ""),
    )


def monitor_status(args, printer):
    """Subscribe to the printer's report topic and stream updates until a terminal state.

    ``printer`` is injected by the caller — this module must not reach up to
    ``bambu_cli.printer`` for an ambient one (see scripts/check_layers.py).
    """
    from bambu_cli.argutils import namespace_get as _namespace_get
    from bambu_cli.utils import emit_json_line

    json_mode = bool(_namespace_get(args, "json", False))
    logger.info("📡 Starting status monitor loop. Press Ctrl+C to stop.")
    if printer.simulation_mode:
        _sleep_fn = _sleep(None)
        for state, pct, event in (("PREPARE", 0, "update"), ("RUNNING", 50, "update"), ("FINISH", 100, "terminal")):
            if json_mode:
                emit_json_line(_status_event({"gcode_state": state, "mc_percent": pct}, event))
            else:
                logger.info(f"🤖 [SIM] Simulated status: State={state}, Progress={pct}%")
            if event != "terminal":
                _sleep_fn(0.5)
        if not json_mode:
            logger.info("🏁 Reached terminal state: FINISH")
        return

    terminal_states = TERMINAL_GCODE_STATES
    received_terminal = threading.Event()
    show_progress_bar = not json_mode and sys.stdout.isatty()
    client = _client_factory(None)(printer)
    userdata: dict = {}
    client.user_data_set(userdata)

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(f"device/{printer.serial}/report")
            push = json.dumps({"pushing": {"sequence_id": get_sequence_id(), "command": "pushall"}})
            client.publish(f"device/{printer.serial}/request", push)
        else:
            logger.error(f"Connection failed: rc={rc}")
            received_terminal.set()

    last_state = [None]
    last_pct = [None]
    merged: dict = {}

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("print"), dict):
                merged.update(data["print"])
                p = merged
                state = p.get("gcode_state", "UNKNOWN")
                pct = p.get("mc_percent", 0)

                if state != last_state[0] or pct != last_pct[0]:
                    if "progress" not in userdata and not show_progress_bar:
                        userdata["progress"] = None
                    if "progress" not in userdata:
                        try:  # pragma: no cover -- rich TTY progress UI
                            from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

                            progress = Progress(
                                TextColumn("[bold blue]Print Status"),
                                BarColumn(),
                                "[progress.percentage]{task.percentage:>3.1f}%",
                                "•",
                                TextColumn("{task.description}"),
                                "•",
                                TimeElapsedColumn(),
                                transient=True,
                            )
                            progress.start()
                            userdata["progress"] = progress
                            userdata["task_id"] = progress.add_task(f"State: {state}", total=100, completed=pct)
                        except ImportError:
                            userdata["progress"] = None

                    if userdata.get("progress"):
                        userdata["progress"].update(userdata["task_id"], completed=pct, description=f"State: {state}")
                    elif json_mode:
                        emit_json_line(_status_event(p, "update"))
                    else:
                        logger.info(f"⏳ Status: State={state}, Progress={pct}%")

                    last_state[0] = state
                    last_pct[0] = pct

                if state in terminal_states:
                    if json_mode:
                        emit_json_line(_status_event(p, "terminal"))
                    else:
                        logger.info(f"🏁 Reached terminal state: {state}")
                    received_terminal.set()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug(f"MQTT decode error: {e}")
        except Exception as e:
            logger.warning(f"MQTT message handling error: {e}")

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        _connect(printer, client)
        client.loop_start()
        while not received_terminal.is_set():
            received_terminal.wait(1.0)
    except KeyboardInterrupt:
        logger.info("\n🛑 Monitor loop stopped by user.")
    finally:
        if userdata.get("progress"):
            try:
                userdata["progress"].stop()
            except Exception:
                pass
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
