from __future__ import annotations

import os

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.models import AlertEvent, DeviceStatus, Telemetry
from app.mqtt_client import mqtt_bridge
from app import extras, faces
from app.schemas import (
    AlertIn,
    AlertOut,
    CommandIn,
    CommandOut,
    DeviceStatusOut,
    HealthOut,
    TelemetryOut,
)
from app.auth_security import (
    LoginBody,
    clear_session_cookie,
    create_session_cookie,
    rate_limit_commands,
    rate_limit_login,
    require_auth,
    session_user,
    verify_password,
    OPERATOR_USER,
    OPERATOR_PASSWORD_HASH,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel.api")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active:
                self.active.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            sockets = list(self.active)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = ConnectionManager()


async def store_mqtt_message(topic: str, kind: str, payload: dict[str, Any] | str) -> None:
    extras.count_msg()
    if kind == "risk" and isinstance(payload, dict):
        # risque IA publie par sentinel-ml (sentinel/ai/<device>/risk) : memoire seulement, pas de stockage
        extras.on_ai_message(topic.split("/")[2], payload)
        return
    async with SessionLocal() as session:
        if kind == "telemetry" and isinstance(payload, dict):
            row = Telemetry(
                device_id=str(payload.get("device_id") or topic.split("/")[1]),
                uptime_ms=payload.get("uptime_ms"),
                temperature=payload.get("temperature"),
                humidity=payload.get("humidity"),
                gas=payload.get("gas"),
                presence=payload.get("presence"),
                buzzer=payload.get("buzzer"),
                led_red=payload.get("led_red"),
                led_green=payload.get("led_green"),
                rssi=payload.get("rssi"),
            )
            session.add(row)
        elif kind == "alerts" and isinstance(payload, dict):
            row = AlertEvent(
                device_id=str(payload.get("device_id") or topic.split("/")[1]),
                type=str(payload.get("type", "unknown")),
                state=str(payload.get("state", "unknown")),
                uptime_ms=payload.get("uptime_ms"),
                raw_json=json.dumps(payload),
                source="mqtt",
            )
            session.add(row)
            # Alarme presence armee : PIR (type=presence, state=detected) ou vision
            try:
                t = str(payload.get("type", ""))
                st = str(payload.get("state", ""))
                if t in ("presence", "vision") and st.startswith("face_"):
                    await extras.on_face_event(
                        str(payload.get("device_id") or topic.split("/")[1]),
                        st,
                    )
                elif t in ("presence", "vision"):
                    await extras.on_presence_event(
                        str(payload.get("device_id") or topic.split("/")[1]),
                        st,
                        source="pir" if t == "presence" else "vision",
                    )
            except Exception:
                logger.exception("hook alarme presence")
        elif kind == "status":
            device_id = topic.split("/")[1] if "/" in topic else settings.default_device_id
            status_value = payload if isinstance(payload, str) else str(payload)
            existing = await session.get(DeviceStatus, device_id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.status = status_value
                existing.updated_at = now
            else:
                session.add(
                    DeviceStatus(device_id=device_id, status=status_value, updated_at=now)
                )
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    loop = asyncio.get_running_loop()
    extras.set_broadcast(manager.broadcast)
    mqtt_bridge.start(loop, manager.broadcast, store_mqtt_message)
    logger.info("SENTINEL-X API started")
    yield
    mqtt_bridge.stop()
    logger.info("SENTINEL-X API stopped")


app = FastAPI(title="SENTINEL-X API", version="1.0.0", lifespan=lifespan)
# IA / alarme presence / MCO (memes regles d'authentification que les autres routes /api/v1)
app.include_router(extras.router, dependencies=[Depends(require_auth)])
app.include_router(faces.router, dependencies=[Depends(require_auth)])


@app.get("/health", response_model=HealthOut)
async def health(db: AsyncSession = Depends(get_db)) -> HealthOut:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        mqtt_connected=mqtt_bridge.connected,
        database=db_status,
    )


@app.get("/api/v1/telemetry", response_model=list[TelemetryOut])
async def list_telemetry(
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> list[Telemetry]:
    result = await db.execute(
        select(Telemetry).order_by(Telemetry.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


@app.get("/api/v1/telemetry/latest", response_model=TelemetryOut | None)
async def latest_telemetry(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> Telemetry | None:
    result = await db.execute(
        select(Telemetry).order_by(Telemetry.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


@app.get("/api/v1/alerts", response_model=list[AlertOut])
async def list_alerts(
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> list[AlertEvent]:
    result = await db.execute(
        select(AlertEvent).order_by(AlertEvent.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


@app.post("/api/v1/alerts", response_model=AlertOut, status_code=201)
async def create_alert(
    body: AlertIn,
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> AlertEvent:
    row = AlertEvent(
        device_id=body.device_id,
        type=body.type,
        state=body.state,
        uptime_ms=body.uptime_ms,
        raw_json=body.model_dump_json(),
        source="api",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await manager.broadcast(
        {
            "channel": "alerts",
            "topic": f"sentinel/{body.device_id}/alerts",
            "data": {
                "device_id": body.device_id,
                "type": body.type,
                "state": body.state,
                "uptime_ms": body.uptime_ms,
                "id": row.id,
            },
        }
    )
    if body.type in ("vision", "presence") and (body.state or "").startswith("face_"):
        await extras.on_face_event(body.device_id, body.state)
    elif body.type in ("vision", "presence"):
        await extras.on_presence_event(
            body.device_id, body.state, source="vision" if body.type == "vision" else "pir"
        )
    return row


@app.get("/api/v1/status", response_model=list[DeviceStatusOut])
async def list_status(
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_auth),
) -> list[DeviceStatus]:
    result = await db.execute(select(DeviceStatus).order_by(DeviceStatus.device_id))
    return list(result.scalars().all())


@app.post("/api/v1/commands", response_model=CommandOut)
async def send_command(
    body: CommandIn,
    request: Request,
    _user: str = Depends(require_auth),
) -> CommandOut:
    rate_limit_commands(request)
    payload: dict[str, Any] = {"action": body.action}

    if body.action == "buzzer_on":
        payload["duration_ms"] = body.duration_ms or 5000
        payload["song"] = body.song or "rickroll"
    elif body.action == "led":
        if body.color is None or body.state is None:
            raise HTTPException(
                status_code=422,
                detail="color and state are required for action=led",
            )
        payload["color"] = body.color
        payload["state"] = body.state

    topic = f"sentinel/{body.device_id}/cmd"
    try:
        mqtt_bridge.publish(topic, payload)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MQTT publish failed: {exc}") from exc

    return CommandOut(ok=True, topic=topic, payload=payload)



@app.post("/api/v1/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, str]:
    rate_limit_login(request)
    if body.username != OPERATOR_USER or not verify_password(body.password, OPERATOR_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    create_session_cookie(response, body.username)
    return {"ok": "true", "user": body.username}


@app.post("/api/v1/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    clear_session_cookie(response)
    return {"ok": "true"}


@app.get("/api/v1/auth/me")
async def auth_me(user: str = Depends(require_auth)) -> dict[str, str]:
    return {"user": user}


@app.get("/login")
async def login_page() -> FileResponse:
    page = STATIC_DIR / "login.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Login page missing")
    return FileResponse(page)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    # Cookie de session (navigateur) ou refus
    cookie = websocket.cookies.get("sentinel_session")
    if not session_user(cookie):
        await websocket.close(code=4401)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)


@app.get("/")
async def dashboard(request: Request):
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not session_user(request.cookies.get("sentinel_session")):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(index)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ------------------------------------------------------------------ camera (proxy authentifie)
# Le flux camera passe par l'API : il faut etre connecte (cookie de session) pour le voir,
# que l'on passe par Caddy (https://.../cam/) ou directement par le port 3000.
_CAM_BASE = os.getenv("VISION_BASE_URL", "http://172.22.0.1:8081")
_CAM_ALLOWED = {"snapshot.jpg": "image/jpeg", "snapshot_raw.jpg": "image/jpeg", "health": "application/json"}


@app.get("/cam/{name}")
async def cam_proxy(name: str, _user: str = Depends(require_auth)):
    import asyncio as _aio
    import urllib.request as _u
    from fastapi import HTTPException as _HE
    from fastapi.responses import Response as _R
    if name not in _CAM_ALLOWED:
        raise _HE(status_code=404, detail="ressource camera inconnue")

    def _get() -> bytes:
        with _u.urlopen(f"{_CAM_BASE}/{name}", timeout=3) as r:
            return r.read()
    try:
        data = await _aio.to_thread(_get)
    except Exception:
        raise _HE(status_code=503, detail="service vision indisponible")
    return _R(content=data, media_type=_CAM_ALLOWED[name], headers={"Cache-Control": "no-store"})
