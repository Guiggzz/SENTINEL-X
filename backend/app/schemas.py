from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TelemetryOut(BaseModel):
    id: int
    device_id: str
    uptime_ms: int | None = None
    temperature: float | None = None
    humidity: float | None = None
    gas: int | None = None
    presence: bool | None = None
    buzzer: bool | None = None
    led_red: bool | None = None
    led_green: bool | None = None
    rssi: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., min_length=1, max_length=64)
    state: str = Field(..., min_length=1, max_length=64)
    uptime_ms: int | None = None


class AlertOut(BaseModel):
    id: int
    device_id: str
    type: str
    state: str
    uptime_ms: int | None = None
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceStatusOut(BaseModel):
    device_id: str
    status: str
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class CommandIn(BaseModel):
    device_id: str = "sentinel-node-01"
    action: Literal["buzzer_on", "buzzer_off", "beep", "led", "led_auto"]
    duration_ms: int | None = Field(default=None, ge=100, le=60000)
    color: Literal["red", "green"] | None = None
    state: bool | None = None
    song: Literal["paquetta", "rickroll", "gaz", "intrus"] | None = None


class CommandOut(BaseModel):
    ok: bool
    topic: str
    payload: dict[str, Any]


class HealthOut(BaseModel):
    status: str
    mqtt_connected: bool
    database: str
