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


# Identifiants stricts : empêchent l'injection de topics MQTT (/, +, #) et de contenu HTML
DEVICE_ID_RE = r"^[A-Za-z0-9_-]{1,32}$"
TOKEN_RE = r"^[A-Za-z0-9_.:-]{1,64}$"


class AlertIn(BaseModel):
    model_config = {"extra": "forbid"}
    device_id: str = Field(..., pattern=DEVICE_ID_RE)
    type: str = Field(..., pattern=TOKEN_RE)
    state: str = Field(..., pattern=TOKEN_RE)
    uptime_ms: int | None = Field(default=None, ge=0, le=2**53)


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
    model_config = {"extra": "forbid"}
    device_id: str = Field(default="sentinel-node-01", pattern=DEVICE_ID_RE)
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
