"""Détection YuNet + empreinte SFace (OpenCV), CPU, modèles locaux.

Sans les fichiers ONNX, ready reste False : la détection YOLO continue seule.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from face_liveness import LivenessConfig, LivenessModel

YUNET_NAME = "face_detection_yunet_2023mar.onnx"
SFACE_NAME = "face_recognition_sface_2021dec.onnx"


@dataclass
class Detection:
    embedding: np.ndarray
    box: tuple[int, int, int, int]
    score: float
    thumb_jpeg: bytes | None = None
    faces: int = 1
    landmarks: tuple[float, ...] | None = None
    portrait_jpeg: bytes | None = None
    # anti-spoofing (embed_bytes) : live_checked=True si le modèle a tourné ou aurait dû
    live_checked: bool = False
    live_score: float | None = None
    live_probs: tuple[float, float, float] | None = None

    @property
    def frontal(self) -> bool:
        """Visage de face : nez à mi-chemin des yeux (YuNet : œil d., œil g., nez, ...)."""
        return frontal_score(self.landmarks) < 0.18


def frontal_score(landmarks: tuple[float, ...] | None) -> float:
    """0 = parfaitement de face ; ~0.5 et plus = profil. 1.0 si inconnu."""
    if not landmarks or len(landmarks) < 6:
        return 1.0
    rx, _ry, lx, _ly, nx, _ny = landmarks[:6]
    eye_dist = abs(lx - rx)
    if eye_dist < 1:
        return 1.0
    return abs(nx - (rx + lx) / 2.0) / eye_dist


def portrait_box(
    box: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int]:
    """Cadre photo d'identité 3:4 autour de la boîte visage YuNet (x, y, w, h).

    Largeur ~2,2 x visage, hauteur ~2,8-2,9 x visage, centre du visage à 45 % du haut,
    puis réduction/décalage pour rester dans l'image. Retourne (x0, y0, x1, y1).
    """
    x, y, bw, bh = box
    bw, bh = max(bw, 1), max(bh, 1)
    cx, cy = x + bw / 2.0, y + bh / 2.0
    w = max(2.2 * bw, 2.8 * bh * 3 / 4)
    h = w * 4 / 3
    scale = min(1.0, width / w, height / h)
    w, h = w * scale, h * scale
    x0 = min(max(cx - w / 2.0, 0.0), width - w)
    y0 = min(max(cy - 0.45 * h, 0.0), height - h)
    return int(round(x0)), int(round(y0)), int(round(x0 + w)), int(round(y0 + h))


class FaceEngine:
    def __init__(self, model_dir: Path, live_cfg: LivenessConfig | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.live_cfg = live_cfg or LivenessConfig.from_env()
        self.live = LivenessModel(self.model_dir)
        self.enroll_live_threshold = self.live_cfg.enroll_threshold
        self.ready = False
        self.detail = "modèles absents — lancer fetch_face_models.py"
        self._cv2 = None
        self._det = None
        self._rec = None
        self._lock = threading.Lock()
        yunet = self.model_dir / YUNET_NAME
        sface = self.model_dir / SFACE_NAME
        if not yunet.is_file() or not sface.is_file():
            return
        try:
            import cv2

            det = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), 0.6, 0.3, 5000)
            rec = cv2.FaceRecognizerSF.create(str(sface), "")
        except Exception as exc:  # noqa: BLE001
            self.detail = f"opencv: {exc}"
            return
        if det is None or rec is None:
            self.detail = "opencv sans FaceDetectorYN / FaceRecognizerSF"
            return
        self._cv2 = cv2
        self._det = det
        self._rec = rec
        self.ready = True
        self.detail = "yunet+sface"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if not self.ready or frame is None:
            return []
        cv2 = self._cv2
        height, width = frame.shape[:2]
        with self._lock:
            self._det.setInputSize((width, height))
            _ok, faces = self._det.detect(frame)
            if faces is None or len(faces) == 0:
                return []
            found: list[Detection] = []
            for row in faces:
                aligned = self._rec.alignCrop(frame, row)
                feat = self._rec.feature(aligned)
                emb = np.asarray(feat, dtype=np.float32).reshape(-1)
                x, y, bw, bh = (int(v) for v in row[:4])
                marks = tuple(float(v) for v in row[4:14])
                found.append(Detection(emb, (x, y, bw, bh), float(row[-1]), landmarks=marks))
            return found

    @property
    def liveness_on(self) -> bool:
        return self.live_cfg.enabled and self.live.ready

    def liveness_probs(self, image: np.ndarray, box) -> np.ndarray | None:
        """[photo, vrai, écran] pour une boîte YuNet ; None si modèle absent ou visage trop petit."""
        if not self.liveness_on or box[2] < self.live_cfg.min_face:
            return None
        return self.live.probs(image, box)

    def embed_bytes(self, data: bytes) -> Detection | None:
        if not self.ready or not data:
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        image = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
        if image is None:
            return None
        found = self.detect(image)
        if not found:
            return None
        best = max(found, key=lambda det: det.box[2] * det.box[3])
        best.thumb_jpeg = self._thumb(image, best.box)
        best.portrait_jpeg = self._portrait(image, best.box)
        # Visages "concurrents" : taille comparable au principal (>= 25 % de sa surface).
        # Les petits visages d'arrière-plan sont ignorés (le plus grand est retenu).
        area = max(best.box[2] * best.box[3], 1)
        best.faces = sum(1 for det in found if det.box[2] * det.box[3] >= 0.25 * area)
        if self.liveness_on:
            best.live_checked = True
            probs = self.liveness_probs(image, best.box)
            if probs is not None:
                best.live_probs = tuple(float(v) for v in probs)
                best.live_score = float(probs[1])
        return best

    def _thumb(self, image: np.ndarray, box: tuple[int, int, int, int]) -> bytes:
        cv2 = self._cv2
        x, y, bw, bh = box
        height, width = image.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + max(bw, 1)), min(height, y + max(bh, 1))
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            crop = image
        thumb = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else b""

    def _portrait(self, image: np.ndarray, box: tuple[int, int, int, int]) -> bytes:
        """Portrait type photo d'identité (tête + haut des épaules), 3:4, 300x400."""
        cv2 = self._cv2
        height, width = image.shape[:2]
        x0, y0, x1, y1 = portrait_box(box, width, height)
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            return b""
        interp = cv2.INTER_AREA if crop.shape[1] >= 300 else cv2.INTER_CUBIC
        out = cv2.resize(crop, (300, 400), interpolation=interp)
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        return buf.tobytes() if ok else b""
