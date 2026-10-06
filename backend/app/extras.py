"""SENTINEL-X - extensions API : etat IA (maintenance predictive), alarme presence armable, MCO hote."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal, get_db
from app.face_gate import face_alarm_action, vision_camera_gated
from app.models import AlertEvent, AppSetting, DeviceStatus
from app.mqtt_client import mqtt_bridge

logger = logging.getLogger("sentinel.extras")
router = APIRouter(prefix="/api/v1")

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]
_broadcast: BroadcastFn | None = None


def set_broadcast(fn: BroadcastFn) -> None:
    global _broadcast
    _broadcast = fn


async def _emit(msg: dict[str, Any]) -> None:
    if _broadcast:
        try:
            await _broadcast(msg)
        except Exception:
            logger.exception("broadcast failed")


async def _log_alert(device_id: str, type_: str, state: str, extra: dict | None = None) -> None:
    async with SessionLocal() as s:
        row = AlertEvent(device_id=device_id, type=type_, state=state, uptime_ms=None,
                         raw_json=json.dumps({"device_id": device_id, "type": type_, "state": state, **(extra or {})}),
                         source="api")
        s.add(row)
        await s.commit()
        await s.refresh(row)
    await _emit({"channel": "alerts", "topic": f"sentinel/{device_id}/alerts",
                 "data": {"device_id": device_id, "type": type_, "state": state, "id": row.id, **(extra or {})}})


# ------------------------------------------------------------------ compteur MQTT (MCO)
_msg_times: deque = deque(maxlen=20000)
_msg_total = 0


def count_msg() -> None:
    global _msg_total
    _msg_total += 1
    _msg_times.append(time.time())


# ------------------------------------------------------------------ IA
AI_LATEST: dict[str, dict[str, Any]] = {}
AI_HISTORY: dict[str, deque] = {}
AI_STALE_S = 15.0


def on_ai_message(device_id: str, payload: dict[str, Any]) -> None:
    now = time.time()
    payload = dict(payload)
    payload["_rx"] = now
    AI_LATEST[device_id] = payload
    if payload.get("risk") is not None:
        AI_HISTORY.setdefault(device_id, deque(maxlen=450)).append([round(now, 1), payload["risk"]])


def gas_alarm_active() -> bool:
    """True seulement pour un vrai nœud (ignore sentinel-test-* des injections de test)."""
    now = time.time()
    for did, p in AI_LATEST.items():
        if did.startswith("sentinel-test"):
            continue
        if p.get("state") == "gaz_fumee" and now - p.get("_rx", 0) < AI_STALE_S and p.get("alarm"):
            return True
    return False


@router.get("/ai")
async def ai_state() -> dict[str, Any]:
    """Dernier etat du modele IA par equipement + historique du risque (15 min)."""
    now = time.time()
    devices = {d: {**p, "age_s": round(now - p["_rx"], 1)} for d, p in AI_LATEST.items() if now - p["_rx"] < 300}
    return {"devices": devices,
            "history": {d: list(h) for d, h in AI_HISTORY.items() if d in devices},
            "gas_alarm": gas_alarm_active()}


# ------------------------------------------------------------------ alarme presence
PERSON_KEY = "person_alarm_enabled"
FACE_KEY = "face_alarm_enabled"
INTRUS_MS = int(os.getenv("PERSON_ALARM_DURATION_MS", "15000"))
_intrus_until = 0.0
_face_siren = False
# Armement : bip de ARM_BEEP_MS, l'alarme n'est active qu'a la fin du bip
ARM_BEEP_MS = int(os.getenv("ARM_BEEP_MS", "2000"))
_armed_at = 0.0


def _vision_health() -> dict[str, Any]:
    import json as _j
    import urllib.request as _u
    try:
        with _u.urlopen(os.getenv("VISION_HEALTH_URL", "http://172.22.0.1:8081/health"), timeout=1.5) as r:
            data = _j.loads(r.read())
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _vision_persons() -> int:
    try:
        return int(_vision_health().get("persons") or 0)
    except (TypeError, ValueError):
        return 0


async def _arm_sequence(reason: str) -> None:
    """Bip d'armement puis, a la fin du bip, verifie si quelqu'un est deja la (PIR ou camera)."""
    global _armed_at
    _armed_at = time.time() + ARM_BEEP_MS / 1000
    try:
        _cmd({"action": "beep", "duration_ms": ARM_BEEP_MS})
    except Exception:
        logger.warning("bip d'armement impossible (MQTT)")
    await _log_alert("sentinel-api", "reglage", "armement_en_cours", {"bip_ms": ARM_BEEP_MS, "motif": reason})
    await asyncio.sleep(ARM_BEEP_MS / 1000 + 0.2)
    async with SessionLocal() as s:
        enabled, _ = await _person_enabled(s)
        srcs = await _load_sources(s)
        from sqlalchemy import select as _sel
        from app.models import Telemetry as _T
        r = await s.execute(_sel(_T).order_by(_T.created_at.desc()).limit(1))
        last = r.scalar_one_or_none()
    if not enabled:
        return
    await _log_alert("sentinel-api", "reglage", "alarme_presence_active")
    await _emit({"channel": "settings", "data": {"person_alarm_active": True}})
    if srcs.get("pir", True) and last is not None and last.presence:
        await on_presence_event(last.device_id, "detected", source="pir_au_armement")
    elif srcs.get("vision", True) and await asyncio.to_thread(_vision_persons) > 0:
        await on_presence_event(settings.default_device_id, "person_detected", source="vision_au_armement")

# Sources qui peuvent declencher une alarme (persistees dans app_settings)
SOURCE_DEFAULTS = {
    "pir": True,          # capteur PIR physique
    "vision": True,       # detection personne YOLO
    "gaz_ia": True,       # Isolation Forest → sirène gaz
    "thermique_ia": True  # Isolation Forest → alerte thermique / LED
}
SOURCES_KEY = "alarm_sources"
SOURCES_TOPIC = "sentinel/config/sources"


class PersonAlarmIn(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool


class SourcesIn(BaseModel):
    model_config = {"extra": "forbid"}
    pir: bool | None = None
    vision: bool | None = None
    gaz_ia: bool | None = None
    thermique_ia: bool | None = None


async def _person_enabled(db: AsyncSession) -> tuple[bool, datetime | None]:
    row = await db.get(AppSetting, PERSON_KEY)
    return (row is not None and row.value == "true"), (row.updated_at if row else None)


async def _face_enabled(db: AsyncSession) -> tuple[bool, datetime | None]:
    row = await db.get(AppSetting, FACE_KEY)
    return (row is not None and row.value == "true"), (row.updated_at if row else None)


def _cmd(payload: dict[str, Any]) -> None:
    mqtt_bridge.publish(f"sentinel/{settings.default_device_id}/cmd", payload)


@router.get("/settings/person-alarm")
async def get_person_alarm(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    enabled, updated = await _person_enabled(db)
    return {"enabled": enabled, "updated_at": updated, "duration_ms": INTRUS_MS, "song": "intrus"}


@router.put("/settings/person-alarm")
async def put_person_alarm(body: PersonAlarmIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    global _intrus_until
    now = datetime.now(timezone.utc)
    row = await db.get(AppSetting, PERSON_KEY)
    if row:
        row.value, row.updated_at = ("true" if body.enabled else "false"), now
    else:
        db.add(AppSetting(key=PERSON_KEY, value="true" if body.enabled else "false", updated_at=now))
    await db.commit()
    if not body.enabled and time.time() < _intrus_until and not gas_alarm_active():
        try:
            _cmd({"action": "buzzer_off"})
        except Exception:
            logger.warning("buzzer_off impossible (MQTT)")
        _intrus_until = 0.0
    await _emit({"channel": "settings", "data": {"person_alarm_enabled": body.enabled, "updated_at": now.isoformat()}})
    await _log_alert("sentinel-api", "reglage", "alarme_presence_armee" if body.enabled else "alarme_presence_desarmee")
    global _armed_at
    if body.enabled:
        asyncio.create_task(_arm_sequence("armement"))
    else:
        _armed_at = 0.0
    return {"enabled": body.enabled, "updated_at": now, "duration_ms": INTRUS_MS, "song": "intrus"}


@router.get("/settings/face-alarm")
async def get_face_alarm(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    enabled, updated = await _face_enabled(db)
    return {"enabled": enabled, "updated_at": updated, "song": "intrus", "duration_ms": INTRUS_MS}


@router.put("/settings/face-alarm")
async def put_face_alarm(body: PersonAlarmIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Alarme visage inconnu, indépendante de l'alarme présence. Désarmée par défaut."""
    global _face_siren, _intrus_until
    now = datetime.now(timezone.utc)
    row = await db.get(AppSetting, FACE_KEY)
    if row:
        row.value, row.updated_at = ("true" if body.enabled else "false"), now
    else:
        db.add(AppSetting(key=FACE_KEY, value="true" if body.enabled else "false", updated_at=now))
    await db.commit()
    if not body.enabled and _face_siren and not gas_alarm_active():
        try:
            _cmd({"action": "buzzer_off"})
        except Exception:
            logger.warning("buzzer_off visage impossible (MQTT)")
        _face_siren = False
        _intrus_until = 0.0
    await _emit({"channel": "settings", "data": {"face_alarm_enabled": body.enabled, "updated_at": now.isoformat()}})
    await _log_alert("sentinel-api", "reglage", "alarme_visage_armee" if body.enabled else "alarme_visage_desarmee")
    if body.enabled:
        health = await asyncio.to_thread(_vision_health)
        if health.get("face_ready") and health.get("face_status") == "unknown":
            await on_face_event(settings.default_device_id, "face_unknown")
        else:
            try:
                _cmd({"action": "beep", "duration_ms": 400})
            except Exception:
                logger.warning("bip visage impossible (MQTT)")
    return {"enabled": body.enabled, "updated_at": now, "song": "intrus", "duration_ms": INTRUS_MS}


async def _load_sources(db: AsyncSession) -> dict[str, bool]:
    row = await db.get(AppSetting, SOURCES_KEY)
    out = dict(SOURCE_DEFAULTS)
    if row and row.value:
        try:
            import json as _json
            data = _json.loads(row.value)
            for k in SOURCE_DEFAULTS:
                if k in data:
                    out[k] = bool(data[k])
        except Exception:
            logger.warning("sources illisibles, defauts")
    return out


def _publish_sources(sources: dict[str, bool]) -> None:
    try:
        mqtt_bridge.publish(SOURCES_TOPIC, sources, retain=True)
    except Exception:
        logger.exception("publish sources")


@router.get("/settings/sources")
async def get_sources(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    src = await _load_sources(db)
    row = await db.get(AppSetting, SOURCES_KEY)
    return {"sources": src, "updated_at": row.updated_at if row else None}


@router.put("/settings/sources")
async def put_sources(body: SourcesIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    src = await _load_sources(db)
    newly_on = [k for k, v in body.model_dump(exclude_none=True).items() if v and not src.get(k) and k in ("pir", "vision")]
    for k, v in body.model_dump(exclude_none=True).items():
        src[k] = bool(v)
    import json as _json
    raw = _json.dumps(src)
    row = await db.get(AppSetting, SOURCES_KEY)
    if row:
        row.value, row.updated_at = raw, now
    else:
        db.add(AppSetting(key=SOURCES_KEY, value=raw, updated_at=now))
    await db.commit()
    _publish_sources(src)
    await _emit({"channel": "settings", "data": {"sources": src, "updated_at": now.isoformat()}})
    await _log_alert("sentinel-api", "reglage", "sources_alarmes", src)
    if newly_on and (await _person_enabled(db))[0]:
        asyncio.create_task(_arm_sequence("source_" + "+".join(newly_on)))
    return {"sources": src, "updated_at": now}

# Etats qui declenchent / arretent l'alarme presence (vision OU PIR)
_TRIG = {"person_detected", "detected", "present", "on"}
_CLEAR = {"person_cleared", "cleared", "absent", "off"}


async def on_presence_event(device_id: str, state: str, source: str = "vision") -> None:
    """Sirene 'intrus' si alarme armee. Accepte les alertes vision et PIR (presence/detected)."""
    global _intrus_until, _face_siren
    st = (state or "").lower()
    # Portillon visage armé et modèles prêts : la caméra ne sonne plus sur une simple personne,
    # et person_cleared ne coupe pas la sirène (c'est face_cleared / face_known qui s'en charge).
    if (source or "").startswith("vision"):
        async with SessionLocal() as s:
            face_on, _ = await _face_enabled(s)
        ready = bool((await asyncio.to_thread(_vision_health)).get("face_ready")) if face_on else False
        if vision_camera_gated(face_on, ready, source, st):
            return
    if st in _TRIG:
        async with SessionLocal() as s:
            enabled, _ = await _person_enabled(s)
            sources = await _load_sources(s)
        if not enabled:
            return
        if time.time() < _armed_at:
            return  # bip d'armement en cours : pas encore actif
        # Mappe la provenance (pir / vision / pir_au_armement) vers la case a cocher
        src_key = "pir" if source.startswith("pir") else ("vision" if source.startswith("vision") else source)
        if src_key in sources and not sources[src_key]:
            return  # source desactivee : evenement ignore pour la sirene
        if gas_alarm_active():
            await _log_alert(device_id, "intrusion", "detectee_pendant_alarme_gaz", {"source": source})
            return
        if time.time() < _intrus_until:
            return  # deja en cours
        try:
            _cmd({"action": "buzzer_on", "duration_ms": INTRUS_MS, "song": "intrus"})
            _intrus_until = time.time() + INTRUS_MS / 1000
        except Exception:
            logger.exception("commande intrus impossible")
        await _log_alert(device_id, "intrusion", "alarme_declenchee",
                         {"song": "intrus", "duration_ms": INTRUS_MS, "source": source})
    elif st in _CLEAR and not source.startswith("pir") and time.time() < _intrus_until and not gas_alarm_active():
        # (PIR : impulsion de 5 s cote firmware -> sa fin ne coupe pas la sirene, elle dure INTRUS_MS)
        try:
            _cmd({"action": "buzzer_off"})
        except Exception:
            logger.exception("buzzer_off impossible")
        _intrus_until = 0.0
        _face_siren = False
        await _log_alert(device_id, "intrusion", "alarme_arretee_zone_libre", {"source": source})


async def on_vision_alert(device_id: str, state: str) -> None:
    await on_presence_event(device_id, state, source="vision")


async def on_face_event(device_id: str, state: str) -> None:
    """Sirène intrus seulement pour un visage inconnu, et seulement si le réglage est armé.

    Visage connu ou absence de visage : pas d'alarme. Si la sirène en cours vient de ce
    portillon, on la coupe. La LED rouge suit le buzzer (mode auto du firmware).
    """
    global _intrus_until, _face_siren
    async with SessionLocal() as s:
        face_on, _ = await _face_enabled(s)
    action = face_alarm_action(face_on, state)
    if action == "ignore":
        return
    if action == "alarm":
        if gas_alarm_active():
            await _log_alert(device_id, "intrusion", "detectee_pendant_alarme_gaz", {"source": "face"})
            return
        if time.time() < _intrus_until:
            return
        try:
            _cmd({"action": "buzzer_on", "duration_ms": INTRUS_MS, "song": "intrus"})
            _intrus_until = time.time() + INTRUS_MS / 1000
            _face_siren = True
        except Exception:
            logger.exception("commande intrus visage impossible")
        await _log_alert(
            device_id,
            "intrusion",
            "alarme_declenchee",
            {"song": "intrus", "duration_ms": INTRUS_MS, "source": "face"},
        )
        return
    if _face_siren and time.time() < _intrus_until and not gas_alarm_active():
        try:
            _cmd({"action": "buzzer_off"})
        except Exception:
            logger.exception("buzzer_off visage impossible")
        _intrus_until = 0.0
        _face_siren = False
        await _log_alert(device_id, "intrusion", "alarme_arretee_visage", {"state": state, "source": "face"})


# ------------------------------------------------------------------ MCO
_cpu_prev: tuple[float, float] | None = None


def _read_cpu() -> tuple[float, float]:
    with open("/proc/stat") as f:
        vals = [float(v) for v in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return idle, sum(vals[:8])


def _cpu_pct() -> float:
    global _cpu_prev
    if _cpu_prev is None:
        _cpu_prev = _read_cpu()
        time.sleep(0.25)
    cur = _read_cpu()
    di, dt = cur[0] - _cpu_prev[0], cur[1] - _cpu_prev[1]
    _cpu_prev = cur
    return round(100.0 * (1 - di / dt), 1) if dt > 0 else 0.0


def _meminfo() -> dict[str, float]:
    m: dict[str, float] = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            m[k] = float(v.split()[0])
    total, avail = m.get("MemTotal", 1), m.get("MemAvailable", 0)
    return {"total_gb": round(total / 1048576, 1), "used_pct": round(100 * (1 - avail / total), 1)}


def _tcp(host: str, port: int) -> float | None:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return None


@router.get("/system")
async def system_metrics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """MCO : metriques hote lues dans /proc (noyau partage avec le conteneur) + sondes des services."""
    cpu = await asyncio.to_thread(_cpu_pct)
    du = shutil.disk_usage("/")
    with open("/proc/uptime") as f:
        up = float(f.read().split()[0])
    t0 = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        db_ms: float | None = round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        db_ms = None
    mq_ms = await asyncio.to_thread(_tcp, settings.mqtt_host, settings.mqtt_port)
    rows = (await db.execute(select(DeviceStatus))).scalars().all()
    st = {r.device_id: r.status for r in rows}
    now = time.time()
    rate = sum(1 for t in _msg_times if now - t < 60)
    ml_fresh = any(now - p["_rx"] < AI_STALE_S for p in AI_LATEST.values())
    return {
        "source": "/proc (hote, vu depuis le conteneur sentinel-api)",
        "cpu_pct": cpu,
        "load": [round(x, 2) for x in os.getloadavg()],
        "cpu_count": os.cpu_count(),
        "mem": _meminfo(),
        "disk": {"total_gb": round(du.total / 1e9, 1), "used_pct": round(100 * du.used / du.total, 1)},
        "uptime_s": int(up),
        "mqtt": {"connected": mqtt_bridge.connected, "msgs_per_min": rate, "msgs_total": _msg_total},
        "services": {
            "sentinel-mosquitto": {"ok": mq_ms is not None and mqtt_bridge.connected, "latency_ms": mq_ms},
            "sentinel-db": {"ok": db_ms is not None, "latency_ms": db_ms},
            "sentinel-ml": {"ok": st.get("ai") == "online" and ml_fresh, "status": st.get("ai", "inconnu")},
            "sentinel-node-01": {"ok": st.get(settings.default_device_id) == "online",
                                 "status": st.get(settings.default_device_id, "inconnu")},
        },
    }
