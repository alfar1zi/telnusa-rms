"""Long-running MQTT collector. Requires paho-mqtt in backend venv."""
from __future__ import annotations

import ssl
import threading
import time
from typing import Iterable

from .config import config, mqtt_configured
from .processor import metrics, now_ms, process_raw_mqtt
from .store import Store

try:
    import paho.mqtt.client as mqtt
except ImportError:  # tests can still run without optional runtime dep installed
    mqtt = None


def _rc_value(rc: object) -> int:
    val = getattr(rc, "value", rc)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 1 if getattr(rc, "is_failure", False) else 0


class MqttCollector:
    def __init__(self, store: Store):
        self.store = store
        self._topics = [t.strip() for t in config.MQTT_TOPIC.split(",") if t.strip()]
        self._thread = None
        self._stop = threading.Event()
        self._connect_error = None
        self._last_state = "DISCONNECTED"

    @property
    def configured(self) -> bool:
        return bool(mqtt and mqtt_configured())

    def start(self) -> None:
        if not self.configured:
            state = "ERROR" if config.MQTT_ENABLED and not mqtt else "NOT CONFIGURED"
            self._set_state(state, last_error=None if state == "NOT CONFIGURED" else "paho-mqtt is not installed")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _set_state(self, state: str, *, last_error: str | None = None, subscribed: Iterable[str] | None = None) -> None:
        # Preserve connect error if we have one and new state is not a successful connection
        if self._connect_error and state not in ("CONNECTED", "SUBSCRIBED"):
            last_error = self._connect_error
        values = {"current_connection_state": state, "last_error": last_error}
        if subscribed is not None:
            values["subscribed_topics"] = list(subscribed)
        if state in ("CONNECTED", "SUBSCRIBED"):
            values["last_connected_at"] = now_ms()
        metrics.set("mqtt", **values)
        self.store.set_integration_state(
            "mqtt",
            state,
            configured=self.configured,
            reachable=state in ("CONNECTED", "SUBSCRIBED"),
            authenticated=None,
            last_success=now_ms() if state in ("CONNECTED", "SUBSCRIBED") else None,
            last_error=last_error,
            metrics=metrics.snapshot()["mqtt"],
        )
        self._last_state = state

    def _build_client(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.MQTT_CLIENT_ID, clean_session=True)
        if config.MQTT_USERNAME:
            client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD or None)
        if config.MQTT_TLS:
            client.tls_set(ca_certs=config.MQTT_CA_FILE or None, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            if config.MQTT_TLS_INSECURE_DEV:
                client.tls_insecure_set(True)
        client.reconnect_delay_set(min_delay=config.MQTT_RECONNECT_MIN_SECONDS, max_delay=config.MQTT_RECONNECT_MAX_SECONDS)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        return client

    def _run(self) -> None:
        while not self._stop.is_set():
            self._set_state("CONNECTING")
            self._client = self._build_client()
            try:
                self._client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
                self._client.loop_forever(retry_first_connection=True)
            except Exception as exc:
                self._set_state("ERROR", last_error=type(exc).__name__)
                metrics.inc("mqtt", "reconnect_count")
                self._stop.wait(config.MQTT_RECONNECT_MAX_SECONDS)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if _rc_value(reason_code) != 0:
            self._connect_error = f"connect_rc={reason_code}"
            self._set_state("ERROR", last_error=self._connect_error)
            return
        self._connect_error = None
        self._set_state("CONNECTED")
        for topic in self._topics:
            client.subscribe(topic, qos=1)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        failed = [str(rc) for rc in reason_codes if _rc_value(rc) >= 128]
        if failed:
            self._connect_error = "subscription rejected"
            self._set_state("ERROR", last_error=self._connect_error)
            return
        self._set_state("SUBSCRIBED", subscribed=self._topics)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if self._stop.is_set():
            self._set_state("DISCONNECTED")
            return
        # Only update last_error if no connect_error preserved
        self._set_state("DISCONNECTED")
        metrics.inc("mqtt", "reconnect_count")

    def _on_message(self, client, userdata, msg):
        process_raw_mqtt(self.store, bytes(msg.payload), topic=str(msg.topic), received_ms=now_ms())


collector: MqttCollector | None = None


def start_collector(store: Store) -> MqttCollector:
    global collector
    collector = MqttCollector(store)
    collector.start()
    return collector