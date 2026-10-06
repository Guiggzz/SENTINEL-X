"""Galerie locale de visages : métadonnées JSON, vecteurs .npy, vignettes.

Démo d'atelier. La similarité est un cosinus (plus haut = plus proche).
Le seuil OpenCV SFace de référence est 0,363 — ce n'est pas une biométrie de production.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass
class Match:
    status: str
    name: str | None
    person_id: str | None
    score: float | None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    va = np.asarray(a, dtype=np.float32).reshape(-1)
    vb = np.asarray(b, dtype=np.float32).reshape(-1)
    if va.shape != vb.shape or va.size == 0:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def identify(
    embedding: np.ndarray,
    gallery: list[tuple[str, str, np.ndarray]],
    threshold: float,
) -> Match:
    """Retourne le meilleur cosinus. Galerie vide → inconnu sans score."""
    if not gallery:
        return Match("unknown", None, None, None)
    best_id: str | None = None
    best_name: str | None = None
    best = -1.0
    for person_id, name, emb in gallery:
        score = cosine(embedding, emb)
        if score > best:
            best = score
            best_id = person_id
            best_name = name
    if best >= threshold:
        return Match("known", best_name, best_id, best)
    return Match("unknown", None, None, best)


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > 40 or any(ord(c) < 32 for c in cleaned):
        raise ValueError("nom invalide")
    return cleaned


def _safe_id(person_id: str | None) -> str | None:
    if not person_id or not _ID.match(person_id):
        return None
    return person_id


class Gallery:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def list_people(self) -> list[dict]:
        with self._lock:
            data = self._read()
        people = []
        for pid, meta in data["people"].items():
            people.append(
                {
                    "id": pid,
                    "name": meta.get("name") or pid,
                    "samples": int(meta.get("samples") or 0),
                    "created_at": meta.get("created_at"),
                    "portrait": self._portrait_version(pid),
                }
            )
        people.sort(key=lambda p: (p.get("created_at") or "", p["name"].casefold()))
        return people

    def count(self) -> int:
        return len(self.list_people())

    def enroll(
        self,
        name: str,
        embeddings: list[np.ndarray],
        thumbs: list[bytes],
        person_id: str | None = None,
        portrait: bytes | None = None,
    ) -> dict:
        """portrait : photo badge (3:4) ; écrite seulement si la personne n'en a pas encore,
        donc c'est la première capture (de face) qui fait foi."""
        cleaned = _clean_name(name)
        if not embeddings or len(embeddings) != len(thumbs):
            raise ValueError("échantillon visage manquant")
        with self._lock:
            data = self._read()
            if person_id is not None:
                pid = _safe_id(person_id)
                if pid is None or pid not in data["people"]:
                    raise ValueError("personne inconnue")
            else:
                pid = self._find_name(data, cleaned) or self._new_id(data)
            folder = self.root / pid
            folder.mkdir(parents=True, exist_ok=True)
            meta = data["people"].get(pid) or {
                "name": cleaned,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "samples": 0,
            }
            meta["name"] = cleaned
            start = int(meta.get("samples") or 0)
            for offset, (emb, thumb) in enumerate(zip(embeddings, thumbs)):
                idx = start + offset
                np.save(folder / f"emb_{idx}.npy", np.asarray(emb, dtype=np.float32).reshape(-1))
                (folder / f"thumb_{idx}.jpg").write_bytes(thumb)
            meta["samples"] = start + len(embeddings)
            if portrait and not (folder / "portrait.jpg").is_file():
                self._write_portrait(folder, portrait)
            data["people"][pid] = meta
            self._write(data)
            return {
                "id": pid,
                "name": cleaned,
                "samples": meta["samples"],
                "created_at": meta["created_at"],
            }

    def delete(self, person_id: str) -> bool:
        pid = _safe_id(person_id)
        if pid is None:
            return False
        with self._lock:
            data = self._read()
            if pid not in data["people"]:
                return False
            data["people"].pop(pid)
            self._write(data)
            folder = self.root / pid
            if folder.is_dir():
                shutil.rmtree(folder)
            return True

    def _folder(self, person_id: str) -> Path | None:
        pid = _safe_id(person_id)
        if pid is None:
            return None
        folder = (self.root / pid).resolve()
        if folder.parent != self.root.resolve() or not folder.is_dir():
            return None
        return folder

    def thumb_file(self, person_id: str) -> Path | None:
        """Première vignette chronologique (thumb_0, tri numérique et non lexical)."""
        folder = self._folder(person_id)
        if folder is None:
            return None
        def index(path: Path) -> int:
            try:
                return int(path.stem.split("_", 1)[1])
            except (IndexError, ValueError):
                return 1 << 30
        thumbs = sorted(folder.glob("thumb_*.jpg"), key=index)
        return thumbs[0] if thumbs else None

    def portrait_file(self, person_id: str) -> Path | None:
        folder = self._folder(person_id)
        if folder is None:
            return None
        path = folder / "portrait.jpg"
        return path if path.is_file() else None

    def set_portrait(self, person_id: str, portrait: bytes) -> bool:
        """Remplace la photo badge seule (ne touche ni empreintes ni vignettes)."""
        if not portrait:
            return False
        with self._lock:
            data = self._read()
            pid = _safe_id(person_id)
            if pid is None or pid not in data["people"]:
                return False
            folder = self._folder(pid)
            if folder is None:
                return False
            self._write_portrait(folder, portrait)
            return True

    def _portrait_version(self, pid: str) -> int:
        path = self.root / pid / "portrait.jpg"
        try:
            return int(path.stat().st_mtime)
        except OSError:
            return 0

    @staticmethod
    def _write_portrait(folder: Path, portrait: bytes) -> None:
        tmp = folder / "portrait.jpg.tmp"
        tmp.write_bytes(portrait)
        os.replace(tmp, folder / "portrait.jpg")

    def match(self, embedding: np.ndarray, threshold: float) -> Match:
        rows: list[tuple[str, str, np.ndarray]] = []
        with self._lock:
            data = self._read()
            items = list(data["people"].items())
        for pid, meta in items:
            folder = self.root / pid
            name = str(meta.get("name") or pid)
            samples = int(meta.get("samples") or 0)
            for idx in range(samples):
                path = folder / f"emb_{idx}.npy"
                if not path.is_file():
                    continue
                rows.append((pid, name, np.load(path)))
        return identify(embedding, rows, threshold)

    def _find_name(self, data: dict, name: str) -> str | None:
        key = name.casefold()
        for pid, meta in data["people"].items():
            if str(meta.get("name") or "").casefold() == key:
                return pid
        return None

    def _new_id(self, data: dict) -> str:
        for _ in range(8):
            pid = "p" + secrets.token_hex(4)
            if pid not in data["people"] and not (self.root / pid).exists():
                return pid
        raise RuntimeError("impossible de créer un identifiant")

    def _read(self) -> dict:
        path = self.root / "index.json"
        if not path.is_file():
            return {"people": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"people": {}}
        people = data.get("people")
        if not isinstance(people, dict):
            return {"people": {}}
        return {"people": people}

    def _write(self, data: dict) -> None:
        path = self.root / "index.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
