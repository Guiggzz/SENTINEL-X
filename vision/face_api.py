"""Enrôlement HTTP : multipart nom + photos, sans framework."""

from __future__ import annotations

from email.parser import BytesParser
from email.policy import default

from face_gallery import Gallery

_PHOTO_FIELDS = {"photo", "photos", "file", "files"}
_MAX_PHOTOS = 5
_MAX_PHOTO_BYTES = 4_000_000


def parse_multipart(content_type: str, body: bytes) -> tuple[str, list[bytes]]:
    if not content_type or "multipart/" not in content_type.lower():
        raise ValueError("multipart requis")
    raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    msg = BytesParser(policy=default).parsebytes(raw)
    if not msg.is_multipart():
        raise ValueError("multipart requis")
    name = ""
    photos: list[bytes] = []
    for part in msg.iter_parts():
        field = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if field in ("name", "person"):
            name = payload.decode("utf-8", "replace")
        elif field in _PHOTO_FIELDS and payload:
            photos.append(payload)
    return name, photos


def enroll_images(gallery: Gallery, embedder, name: str, images: list[bytes]) -> tuple[int, dict]:
    if not getattr(embedder, "ready", False):
        return 503, {"error": "modèles de reconnaissance absents"}
    if not (name or "").strip():
        return 422, {"error": "nom requis"}
    embeddings = []
    thumbs: list[bytes] = []
    for blob in images[:_MAX_PHOTOS]:
        if not blob or len(blob) > _MAX_PHOTO_BYTES:
            continue
        det = embedder.embed_bytes(blob)
        if det is None or getattr(det, "embedding", None) is None:
            continue
        embeddings.append(det.embedding)
        thumbs.append(det.thumb_jpeg or b"")
    if not embeddings:
        return 422, {"error": "aucun visage détecté. Photo de face, nette, une personne."}
    try:
        person = gallery.enroll(name, embeddings, thumbs)
    except ValueError as exc:
        return 422, {"error": str(exc)}
    return 201, person
