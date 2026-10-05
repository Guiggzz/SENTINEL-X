"""Proxy authentifié vers la galerie du service vision (hôte :8081)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PERSON_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1/faces")

_VISION = os.getenv("VISION_BASE_URL", "http://172.22.0.1:8081").rstrip("/")
_TOKEN = (os.getenv("API_TOKEN") or "").strip()
_MAX_BODY = 12_000_000


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
    return await _proxy("POST", "/faces", body, ctype, timeout=20)


@router.delete("/{person_id}")
async def delete_face(person_id: str) -> Response:
    if not _PERSON_ID.match(person_id):
        return _bad_id()
    return await _proxy("DELETE", f"/faces/{person_id}")
