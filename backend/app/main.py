from __future__ import annotations

import os

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.models import AlertEvent, DeviceStatus, Telemetry
from app.mqtt_client import mqtt_bridge
from app import extras, faces
from app.face_gate import IDENTITY_STATES
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
    login_failed,
    login_succeeded,
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
                if t == "vision" and st in IDENTITY_STATES:
                    await extras.on_identity_event(str(payload.get("device_id") or topic.split("/")[1]), st)
                elif t in ("presence", "vision") and st.startswith("face_"):
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


# Prod : pas de /docs, /redoc ni /openapi.json (réduction de la surface de reconnaissance)
app = FastAPI(
    title="SENTINEL-X API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

security_log = logging.getLogger("sentinel.security")

# ------------------------------------------------------------------ durcissement HTTP
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_MAX_BODY_DEFAULT = 64 * 1024          # JSON (login, commandes, réglages)
_MAX_BODY_FACES = 12 * 1024 * 1024     # enrôlement visages (multipart, 5 photos max)


def _same_origin(request: Request) -> bool:
    """Anti-CSRF : Origin (ou à défaut Referer) doit correspondre à l'hôte demandé."""
    host = (request.headers.get("host") or "").lower()
    src = request.headers.get("origin") or request.headers.get("referer")
    if not src or src == "null":
        return False
    return urlsplit(src).netloc.lower() == host


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    method = request.method.upper()
    path = request.url.path
    if method in _UNSAFE:
        # 1) taille du corps (Caddy limite aussi en amont)
        limit = _MAX_BODY_FACES if path.startswith("/api/v1/faces") else _MAX_BODY_DEFAULT
        try:
            clen = int(request.headers.get("content-length") or "0")
        except ValueError:
            return JSONResponse({"detail": "Requête invalide"}, status_code=400)
        if clen > limit:
            return JSONResponse({"detail": "Requête trop volumineuse"}, status_code=413)
        # 2) CSRF : requêtes authentifiées par cookie => Origin/Referer obligatoire et identique.
        #    (les clients machine en Bearer, sans cookie, ne sont pas concernés)
        has_cookie = "sentinel_session" in request.cookies
        bearer = (request.headers.get("authorization") or "").lower().startswith("bearer ")
        has_src = bool(request.headers.get("origin") or request.headers.get("referer"))
        if (has_cookie and not bearer) or has_src:
            if not _same_origin(request):
                security_log.warning(
                    "CSRF_BLOCK method=%s path=%s origin=%s",
                    method, path[:64], (request.headers.get("origin") or request.headers.get("referer") or "-")[:64],
                )
                return JSONResponse({"detail": "Origine refusée"}, status_code=403)
    response = await call_next(request)
    # En-têtes de sécurité aussi en accès direct :3000 (Caddy les fixe pour :443)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if path.startswith("/api/") or path.startswith("/cam/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Erreur"
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Pas d'écho des valeurs envoyées : uniquement les champs en erreur
    fields = sorted({".".join(str(x) for x in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
    return JSONResponse({"detail": "Requête invalide", "champs": fields[:10]}, status_code=422)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("erreur non gérée sur %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Erreur interne"}, status_code=500)

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
    if body.type == "vision" and body.state in IDENTITY_STATES:
        await extras.on_identity_event(body.device_id, body.state)
    elif body.type in ("vision", "presence") and (body.state or "").startswith("face_"):
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
    elif body.action == "display":
        payload["mode"] = body.mode
        if body.duration_ms is not None:
            payload["duration_ms"] = body.duration_ms
        if body.name is not None:
            payload["name"] = body.name
    elif body.action == "beep_pattern":
        payload["pattern"] = body.pattern

    topic = f"sentinel/{body.device_id}/cmd"
    try:
        mqtt_bridge.publish(topic, payload)
    except Exception as exc:
        logger.warning("MQTT publish failed: %s", exc)
        raise HTTPException(status_code=503, detail="Broker MQTT indisponible") from exc

    return CommandOut(ok=True, topic=topic, payload=payload)



@app.post("/api/v1/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, str]:
    rate_limit_login(request)
    # bcrypt évalué même si l'utilisateur est faux (pas d'oracle temporel sur l'identifiant)
    pw_ok = verify_password(body.password, OPERATOR_PASSWORD_HASH)
    if not (hmac.compare_digest(body.username, OPERATOR_USER) and pw_ok):
        login_failed(request, body.username)
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    login_succeeded(request, body.username)
    create_session_cookie(response, body.username)
    return {"ok": "true", "user": body.username}


@app.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    clear_session_cookie(response, request.cookies.get("sentinel_session"))
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
    origin = websocket.headers.get("origin")
    host = (websocket.headers.get("host") or "").lower()
    # Anti Cross-Site WebSocket Hijacking : Origin (si présent) doit être le même hôte
    if origin and urlsplit(origin).netloc.lower() != host:
        await websocket.close(code=4403)
        return
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
