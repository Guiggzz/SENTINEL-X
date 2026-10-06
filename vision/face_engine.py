"""Détection YuNet + empreinte SFace (OpenCV), CPU, modèles locaux.

Sans les fichiers ONNX, ready reste False : la détection YOLO continue seule.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

YUNET_NAME = "face_detection_yunet_2023mar.onnx"
SFACE_NAME = "face_recognition_sface_2021dec.onnx"


@dataclass
class Detection:
    embedding: np.ndarray
    box: tuple[int, int, int, int]
    score: float
    thumb_jpeg: bytes | None = None
    faces: int = 1


class FaceEngine:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
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
                found.append(Detection(emb, (x, y, bw, bh), float(row[-1])))
            return found

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
        # Visages "concurrents" : taille comparable au principal (>= 25 % de sa surface).
        # Les petits visages d'arrière-plan sont ignorés (le plus grand est retenu).
        area = max(best.box[2] * best.box[3], 1)
        best.faces = sum(1 for det in found if det.box[2] * det.box[3] >= 0.25 * area)
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
