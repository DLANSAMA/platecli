"""A long-lived MQTT client for one ``BambuPrinter``.

One-shot CLI commands still connect and tear down in ``mqtt_cmd``. The TUI
(and any other long-lived process) calls ``BambuPrinter.hold_mqtt()`` so
``status`` / ``send_command`` / ``get_version`` reuse a single TLS session
instead of opening a new one on every dashboard refresh.

Threading: operations are serialized on ``_op_lock``. paho callbacks run on
the network thread and only touch Events / the merged print dict — they never
take ``_op_lock``. Reconnect cannot re-issue a state-changing command: each
``send_command`` has a once-flag that survives ``on_connect`` firing again.
"""

from __future__ import annotations

import json
import ssl
import threading
from typing import Any, Callable

from bambu_cli.errors import PrinterStatusIncomplete
from bambu_cli.logging_utils import logger
from bambu_cli.protocols.mqtt_cmd import _REQUIRED_STATUS_KEYS, status_is_complete
from bambu_cli.utils import get_sequence_id

ClientFactory = Callable[[Any], Any]


def _sleep_fn(sleep: Callable[[float], None] | None):
    if sleep is not None:
        return sleep
    from bambu_cli.protocols import mqtt as mqtt_mod

    return mqtt_mod.time.sleep


class MqttSession:
    """One paho client, reused until ``close()``."""

    def __init__(
        self,
        printer: Any,
        *,
        client_factory: ClientFactory | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._printer = printer
        self._client_factory = client_factory
        self._sleep = sleep
        self._op_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._client: Any = None
        self._live = False
        self._print_state: dict[str, Any] = {}
        self._awaiting_status = False
        self._awaiting_require_complete = True
        self._status_event = threading.Event()
        self._pending_payload: str | None = None
        self._command_issued = False
        self._publish_ok = False
        self._publish_event = threading.Event()
        self._version_modules: Any = None
        self._version_event = threading.Event()
        self._connect_failed = False
        self._connected_event = threading.Event()

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._printer)
        from bambu_cli.protocols import mqtt as mqtt_mod

        return mqtt_mod.create_mqtt_client(self._printer)

    def _connect(self, client: Any) -> None:
        from bambu_cli.protocols import mqtt as mqtt_mod

        mqtt_mod._mqtt_connect(self._printer, client)

    def close(self) -> None:
        """Stop the loop and disconnect. Safe to call twice."""
        with self._op_lock:
            self._reset_client()

    def _reset_client(self) -> None:
        client = self._client
        self._client = None
        self._live = False
        self._connect_failed = False
        with self._state_lock:
            self._print_state = {}
        if client is None:
            return
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    def _bind_callbacks(self, client: Any) -> None:
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_publish = self._on_publish

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        rc: int,
        properties: Any = None,
    ) -> None:
        if rc == 0:
            self._live = True
            self._connect_failed = False
            try:
                client.subscribe(f"device/{self._printer.serial}/report")
            except Exception as exc:
                logger.debug(f"MQTT session subscribe failed: {exc}")
            self._issue_pending()
        else:
            self._live = False
            self._connect_failed = True
            logger.error(f"Connection failed: rc={rc}")
        self._connected_event.set()

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        rc: Any = None,
        properties: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._live = False

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            logger.debug(f"MQTT decode error: {exc}")
            return
        if not isinstance(data, dict):
            return
        info = data.get("info")
        if isinstance(info, dict) and info.get("command") == "get_version" and "module" in info:
            self._version_modules = info["module"]
            self._version_event.set()
        print_data = data.get("print")
        if not isinstance(print_data, dict):
            return
        with self._state_lock:
            self._print_state.update(print_data)
            complete = status_is_complete(self._print_state)
            if self._awaiting_status and (complete or not self._awaiting_require_complete):
                self._awaiting_status = False
                self._status_event.set()

    def _on_publish(
        self,
        client: Any,
        userdata: Any,
        mid: Any,
        reason_code: Any = None,
        properties: Any = None,
    ) -> None:
        self._publish_ok = True
        self._publish_event.set()

    def _issue_pending(self) -> None:
        if not self._live or self._pending_payload is None or self._command_issued:
            return
        if self._client is None:
            return
        self._command_issued = True
        self._client.publish(
            f"device/{self._printer.serial}/request",
            self._pending_payload,
            qos=1,
        )

    def _publish_pushall(self) -> None:
        if self._client is None:
            return
        push = json.dumps({"pushing": {"sequence_id": get_sequence_id(), "command": "pushall"}})
        self._client.publish(f"device/{self._printer.serial}/request", push)

    def _arm_status_wait(self, require_complete: bool) -> threading.Event:
        with self._state_lock:
            self._awaiting_status = True
            self._awaiting_require_complete = require_complete
            self._status_event = threading.Event()
        return self._status_event

    def _snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._print_state)

    def ensure_connected(self, timeout: float) -> bool:
        """Connect (or reconnect after a drop). Returns False on broker rc != 0."""
        if self._client is not None and self._live:
            return True
        self._reset_client()
        client = self._make_client()
        if hasattr(client, "user_data_set"):
            client.user_data_set({})
        self._connected_event = threading.Event()
        self._bind_callbacks(client)
        self._client = client
        self._connect(client)
        try:
            client.loop_start()
        except Exception:
            pass
        # connect() may already have fired on_connect (tests); real paho fires
        # after loop_start. Wait so we never publish before the subscribe.
        if not self._connected_event.wait(timeout):
            return False
        return self._live

    def get_status(
        self,
        timeout: float,
        retries: int = 2,
        *,
        require_complete: bool = True,
    ) -> dict[str, Any] | None:
        sleeper = _sleep_fn(self._sleep)
        with self._op_lock:
            for attempt in range(retries + 1):
                try:
                    if not self.ensure_connected(timeout):
                        return None
                    event = self._arm_status_wait(require_complete)
                    self._publish_pushall()
                    if event.wait(timeout):
                        snapshot = self._snapshot()
                        if snapshot and (not require_complete or status_is_complete(snapshot)):
                            return snapshot
                    if attempt < retries:
                        with self._state_lock:
                            saw_partial = bool(self._print_state)
                        if saw_partial:
                            logger.warning(
                                f"Printer sent only partial status on attempt {attempt + 1}. "
                                "Re-requesting full state..."
                            )
                        else:
                            logger.warning(f"MQTT status timeout on attempt {attempt + 1}. Retrying...")
                        sleeper(2**attempt)
                except (OSError, ssl.SSLError) as exc:
                    self._reset_client()
                    if attempt < retries:
                        logger.warning(f"MQTT status attempt {attempt + 1} failed: {exc}. Retrying...")
                        sleeper(2**attempt)
                    else:
                        logger.error(f"MQTT status error: {exc}")
            partial = self._snapshot()
            if partial and require_complete:
                missing = [key for key in _REQUIRED_STATUS_KEYS if key not in partial]
                raise PrinterStatusIncomplete(
                    "Printer returned only partial status updates, never a full snapshot "
                    f"(missing {', '.join(missing)}). It may be busy mid-print; retry the command.",
                    detail={"missing_keys": missing, "received_keys": sorted(partial)},
                    next_command="plate status",
                )
            return None

    def send_command(self, payload: str, timeout: float, retries: int = 2) -> bool:
        sleeper = _sleep_fn(self._sleep)
        with self._op_lock:
            for attempt in range(retries + 1):
                try:
                    self._pending_payload = payload
                    self._command_issued = False
                    self._publish_ok = False
                    self._publish_event = threading.Event()
                    if not self.ensure_connected(timeout):
                        self._pending_payload = None
                        return False
                    self._issue_pending()
                    if self._publish_event.wait(timeout):
                        ok = self._publish_ok
                        self._pending_payload = None
                        return ok
                    if attempt < retries:
                        logger.warning(f"MQTT command timeout on attempt {attempt + 1}. Retrying...")
                        sleeper(2**attempt)
                except (OSError, ssl.SSLError) as exc:
                    self._reset_client()
                    if attempt < retries:
                        logger.warning(f"MQTT command attempt {attempt + 1} failed: {exc}. Retrying...")
                        sleeper(2**attempt)
                    else:
                        logger.error(f"MQTT command error: {exc}")
            self._pending_payload = None
            return False

    def get_version(self, timeout: float, retries: int = 1) -> Any:
        sleeper = _sleep_fn(self._sleep)
        request = json.dumps({"info": {"sequence_id": get_sequence_id(), "command": "get_version"}})
        with self._op_lock:
            for attempt in range(retries + 1):
                try:
                    self._version_modules = None
                    self._version_event = threading.Event()
                    if not self.ensure_connected(timeout):
                        return None
                    if self._client is not None:
                        self._client.publish(f"device/{self._printer.serial}/request", request)
                    if self._version_event.wait(timeout):
                        return self._version_modules
                    if attempt < retries:
                        sleeper(2**attempt)
                except (OSError, ssl.SSLError):
                    self._reset_client()
                    if attempt < retries:
                        sleeper(2**attempt)
            return None
