import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
# Nom affiché sur l'OLED (commande display/authorized) : ASCII strict, 16 caractères max
DISPLAY_NAME_RE = r"^[A-Za-z0-9 -]{1,16}$"
DISPLAY_MIN_MS, DISPLAY_MAX_MS = 500, 30000


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
    action: Literal["buzzer_on", "buzzer_off", "beep", "led", "led_auto", "display", "beep_pattern"]
    duration_ms: int | None = Field(default=None, ge=100, le=60000)
    color: Literal["red", "green"] | None = None
    state: bool | None = None
    song: Literal["paquetta", "rickroll", "gaz", "intrus"] | None = None
    # action=display (écran d'alerte OLED)
    mode: Literal["identify", "authorized", "intrusion", "normal"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=16, pattern=DISPLAY_NAME_RE)
    # action=beep_pattern
    pattern: Literal["identify"] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _ascii_name(cls, v: Any) -> Any:
        # « Hélène » -> « Helene » (accents retirés) ; tout autre caractère reste refusé par le motif
        if isinstance(v, str):
            v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii").strip()
        return v

    @model_validator(mode="after")
    def _check_action_fields(self) -> "CommandIn":
        if self.action == "display":
            if self.mode is None:
                raise ValueError("mode is required for action=display")
            if self.duration_ms is not None and not DISPLAY_MIN_MS <= self.duration_ms <= DISPLAY_MAX_MS:
                raise ValueError(f"duration_ms must be {DISPLAY_MIN_MS}..{DISPLAY_MAX_MS} for action=display")
            if self.name is not None and self.mode != "authorized":
                raise ValueError("name is only allowed with mode=authorized")
            if self.mode == "normal" and self.duration_ms is not None:
                raise ValueError("duration_ms is not allowed with mode=normal")
        elif self.mode is not None or self.name is not None:
            raise ValueError("mode/name are only allowed with action=display")
        if self.action == "beep_pattern":
            if self.pattern is None:
                raise ValueError("pattern is required for action=beep_pattern")
            if self.duration_ms is not None:
                raise ValueError("duration_ms is not allowed with action=beep_pattern")
        elif self.pattern is not None:
            raise ValueError("pattern is only allowed with action=beep_pattern")
        if self.action in ("display", "beep_pattern") and (
            self.color is not None or self.state is not None or self.song is not None
        ):
            raise ValueError("color/state/song are not allowed with this action")
        return self


class CommandOut(BaseModel):
    ok: bool
    topic: str
    payload: dict[str, Any]


class HealthOut(BaseModel):
    status: str
    mqtt_connected: bool
    database: str
