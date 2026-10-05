from __future__ import annotations

import asyncio
import ssl
import json
import logging
from typing import Any, Callable, Awaitable

import paho.mqtt.client as mqtt

from app.config import settings

logger = logging.getLogger("sentinel.mqtt")

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]
StoreFn = Callable[[str, str, dict[str, Any] | str], Awaitable[None]]


class MqttBridge:
    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._broadcast: BroadcastFn | None = None
        self._store: StoreFn | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        broadcast: BroadcastFn,
        store: StoreFn,
    ) -> None:
        self._loop = loop
        self._broadcast = broadcast
        self._store = store

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="sentinel-api",
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
        if settings.mqtt_tls:
            ctx = ssl.create_default_context(cafile=settings.mqtt_ca_file or None)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            client.tls_set_context(ctx)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        self._client = client
        client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        client.loop_start()
        logger.info(
            "MQTT bridge starting toward %s:%s tls=%s user=%s",
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_tls,
            bool(settings.mqtt_username),
        )

    def stop(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False

    def publish(self, topic: str, payload: dict[str, Any] | str, qos: int = 0, retain: bool = False) -> None:
        if not self._client:
            raise RuntimeError("MQTT client not started")
        body = payload if isinstance(payload, str) else json.dumps(payload)
        result = self._client.publish(topic, body, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed rc={result.rc}")

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if reason_code == 0 or str(reason_code) in ("Success", "0"):
            self._connected = True
            topics = [
                (settings.mqtt_telemetry_topic, 0),
                (settings.mqtt_alerts_topic, 0),
                (settings.mqtt_status_topic, 0),
                (settings.mqtt_ai_topic, 0),
            ]
            client.subscribe(topics)
            logger.info("MQTT connected, subscribed to %s", [t[0] for t in topics])
        else:
            self._connected = False
            logger.error("MQTT connect failed: %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected = False
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        if not self._loop or not self._store or not self._broadcast:
            return
        topic = msg.topic
        raw = msg.payload.decode("utf-8", errors="replace")
        parts = topic.split("/")
        kind = parts[-1] if parts else ""

        payload: dict[str, Any] | str
        if kind == "status":
            payload = raw.strip()
        else:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON on %s: %s", topic, raw[:200])
                return

        asyncio.run_coroutine_threadsafe(
            self._handle(topic, kind, payload),
            self._loop,
        )

    async def _handle(
        self,
        topic: str,
        kind: str,
        payload: dict[str, Any] | str,
    ) -> None:
        assert self._store and self._broadcast
        try:
            await self._store(topic, kind, payload)
        except Exception:
            logger.exception("Failed to store MQTT message from %s", topic)
            return

        event = {"channel": kind, "topic": topic, "data": payload}
        try:
            await self._broadcast(event)
        except Exception:
            logger.exception("Failed to broadcast MQTT message from %s", topic)


mqtt_bridge = MqttBridge()
