"""Enrôlement HTTP : multipart nom + photos, sans framework."""

from __future__ import annotations

from email.parser import BytesParser
from email.policy import default

from face_gallery import Gallery

_PHOTO_FIELDS = {"photo", "photos", "file", "files"}
_MAX_PHOTOS = 5
_MAX_PHOTO_BYTES = 4_000_000


def parse_multipart(content_type: str, body: bytes) -> tuple[str, list[bytes]]:
    name, photos, _opts = parse_multipart_opts(content_type, body)
    return name, photos


def parse_multipart_opts(content_type: str, body: bytes) -> tuple[str, list[bytes], dict[str, str]]:
    """Comme parse_multipart, plus les champs texte annexes (ex. single=1)."""
    if not content_type or "multipart/" not in content_type.lower():
        raise ValueError("multipart requis")
    raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    msg = BytesParser(policy=default).parsebytes(raw)
    if not msg.is_multipart():
        raise ValueError("multipart requis")
    name = ""
    photos: list[bytes] = []
    opts: dict[str, str] = {}
    for part in msg.iter_parts():
        field = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if field in ("name", "person"):
            name = payload.decode("utf-8", "replace")
        elif field in _PHOTO_FIELDS and payload:
            photos.append(payload)
        elif field in ("single",):
            opts[field] = payload.decode("utf-8", "replace").strip()
    return name, photos, opts


def enroll_images(
    gallery: Gallery, embedder, name: str, images: list[bytes], single: bool = False
) -> tuple[int, dict]:
    """single=True (capture caméra) : refuse une image contenant plusieurs visages."""
    if not getattr(embedder, "ready", False):
        return 503, {"error": "modèles de reconnaissance absents"}
    if not (name or "").strip():
        return 422, {"error": "nom requis"}
    embeddings = []
    thumbs: list[bytes] = []
    portrait: bytes | None = None
    portrait_frontal = False
    for blob in images[:_MAX_PHOTOS]:
        if not blob or len(blob) > _MAX_PHOTO_BYTES:
            continue
        det = embedder.embed_bytes(blob)
        if det is None or getattr(det, "embedding", None) is None:
            continue
        faces = int(getattr(det, "faces", 1) or 1)
        if single and faces > 1:
            return 422, {"error": f"{faces} visages de taille comparable. Une seule personne au premier plan.", "faces": faces}
        embeddings.append(det.embedding)
        thumbs.append(det.thumb_jpeg or b"")
        # photo badge : la première photo de face du lot (sinon la première tout court)
        cand = getattr(det, "portrait_jpeg", None)
        frontal = bool(getattr(det, "frontal", False))
        if cand and (portrait is None or (frontal and not portrait_frontal)):
            portrait, portrait_frontal = cand, frontal
    if not embeddings:
        return 422, {"error": "aucun visage détecté. Photo de face, nette, une personne."}
    try:
        person = gallery.enroll(name, embeddings, thumbs, portrait=portrait)
    except ValueError as exc:
        return 422, {"error": str(exc)}
    return 201, person


def set_portrait_image(gallery: Gallery, embedder, person_id: str, images: list[bytes]) -> tuple[int, dict]:
    """Refait la photo badge depuis une image (une seule personne, de face). Empreintes inchangées."""
    if not getattr(embedder, "ready", False):
        return 503, {"error": "modèles de reconnaissance absents"}
    blob = images[0] if images else b""
    if not blob or len(blob) > _MAX_PHOTO_BYTES:
        return 422, {"error": "photo manquante"}
    det = embedder.embed_bytes(blob)
    if det is None or not getattr(det, "portrait_jpeg", None):
        return 422, {"error": "aucun visage détecté. Se placer de face, à ~1 m de la caméra."}
    faces = int(getattr(det, "faces", 1) or 1)
    if faces > 1:
        return 422, {"error": f"{faces} visages. Une seule personne au premier plan.", "faces": faces}
    if not getattr(det, "frontal", True):
        return 422, {"error": "visage de profil. Regarder la caméra, de face."}
    if not gallery.set_portrait(person_id, det.portrait_jpeg):
        return 404, {"error": "personne introuvable"}
    return 200, {"ok": True}
