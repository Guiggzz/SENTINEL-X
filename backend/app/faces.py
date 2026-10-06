"""Proxy authentifié vers la galerie du service vision (hôte :8081)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from email.parser import BytesParser
from email.policy import default as _email_policy
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PERSON_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1/faces")

_VISION = os.getenv("VISION_BASE_URL", "http://172.22.0.1:8081").rstrip("/")
_TOKEN = (os.getenv("API_TOKEN") or "").strip()
_MAX_BODY = 12_000_000
_MAX_PHOTOS = 5
_MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 Mo par photo
_PHOTO_FIELDS = {"photo", "photos", "file", "files"}
_NAME_RE = re.compile(r"^[\w À-ÿ'.-]{1,40}$")


def _is_image(blob: bytes) -> bool:
    """Signatures (magic bytes) JPEG / PNG / WebP uniquement — le décodage réel est fait par vision (cv2)."""
    return (
        blob[:3] == b"\xff\xd8\xff"
        or blob[:8] == b"\x89PNG\r\n\x1a\n"
        or (blob[:4] == b"RIFF" and blob[8:12] == b"WEBP")
    )


def validate_upload(content_type: str, body: bytes) -> str | None:
    """Retourne un message d'erreur, ou None si l'envoi est acceptable."""
    if not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type:
        return "multipart/form-data requis"
    try:
        raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        msg = BytesParser(policy=_email_policy).parsebytes(raw)
    except Exception:
        return "requête invalide"
    if not msg.is_multipart():
        return "multipart/form-data requis"
    photos = 0
    for part in msg.iter_parts():
        field = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if field in ("name", "person"):
            name = payload.decode("utf-8", "replace").strip()
            if not _NAME_RE.match(name):
                return "nom invalide (1-40 caractères, lettres/chiffres/espace/'-.)"
        elif field in _PHOTO_FIELDS:
            photos += 1
            if photos > _MAX_PHOTOS:
                return f"{_MAX_PHOTOS} photos maximum"
            if len(payload) > _MAX_PHOTO_BYTES:
                return "photo trop volumineuse (5 Mo max)"
            if not _is_image(payload):
                return "format refusé (JPEG, PNG ou WebP uniquement)"
        elif field not in ("single",):
            return "champ inattendu"
    if photos == 0:
        return "aucune photo"
    return None


def _auth_headers(content_type: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if _TOKEN:
        headers["Authorization"] = f"Bearer {_TOKEN}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _call(method: str, path: str, body: bytes | None, content_type: str | None, timeout: float) -> tuple[int, bytes, str]:
    req = Request(
        f"{_VISION}{path}",
        data=body,
        headers=_auth_headers(content_type),
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            media = resp.headers.get("Content-Type", "application/json")
            return resp.status, resp.read(), media
    except HTTPError as exc:
        media = exc.headers.get("Content-Type", "application/json") if exc.headers else "application/json"
        return exc.code, exc.read(), media
    except (URLError, TimeoutError, OSError):
        payload = json.dumps({"error": "service vision indisponible"}).encode()
        return 503, payload, "application/json"


async def _proxy(method: str, path: str, body: bytes | None = None, content_type: str | None = None, timeout: float = 8) -> Response:
    status, data, media = await asyncio.to_thread(_call, method, path, body, content_type, timeout)
    media_type = media.split(";", 1)[0].strip() or "application/json"
    return Response(content=data, status_code=status, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.get("")
async def list_faces() -> Response:
    return await _proxy("GET", "/faces")


@router.get("/status")
async def face_status() -> Response:
    return await _proxy("GET", "/faces/status", timeout=3)


def _bad_id() -> Response:
    return Response(
        content=json.dumps({"error": "identifiant invalide"}).encode(),
        status_code=404,
        media_type="application/json",
    )


@router.get("/{person_id}/thumb")
async def face_thumb(person_id: str) -> Response:
    if not _PERSON_ID.match(person_id):
        return _bad_id()
    return await _proxy("GET", f"/faces/{person_id}/thumb", timeout=4)


@router.post("")
async def enroll_face(request: HttpRequest) -> Response:
    body = await request.body()
    if len(body) > _MAX_BODY:
        return Response(
            content=json.dumps({"error": "photo trop volumineuse"}).encode(),
            status_code=413,
            media_type="application/json",
        )
    ctype = request.headers.get("content-type", "")
    err = validate_upload(ctype, body)
    if err:
        return Response(
            content=json.dumps({"error": err}).encode(),
            status_code=413 if "volumineuse" in err else 422,
            media_type="application/json",
        )
    return await _proxy("POST", "/faces", body, ctype, timeout=20)


@router.delete("/{person_id}")
async def delete_face(person_id: str) -> Response:
    if not _PERSON_ID.match(person_id):
        return _bad_id()
    return await _proxy("DELETE", f"/faces/{person_id}")
