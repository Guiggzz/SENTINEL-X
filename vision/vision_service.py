#!/usr/bin/env python3
"""SENTINEL-X vision service: webcam + YOLO → MJPEG + API alerts."""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from face_api import enroll_images, parse_multipart_opts, set_portrait_image
from face_engine import FaceEngine
from face_gallery import Gallery

# Cap CPU threads BEFORE loading torch/ultralytics
_nt = int(os.environ.get("TORCH_NUM_THREADS", "2"))
os.environ.setdefault("OMP_NUM_THREADS", str(_nt))
os.environ.setdefault("MKL_NUM_THREADS", str(_nt))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_nt))

import torch

torch.set_num_threads(max(1, _nt))
try:
    torch.set_num_interop_threads(1)
except Exception:
    pass

import cv2

cv2.setNumThreads(1)
import numpy as np
from ultralytics import YOLO

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.vision")

# Jamais 0.0.0.0 : par défaut loopback ; en prod = IP de la passerelle du réseau Docker
# sentinel-front (172.22.0.1) pour que seule l'API (conteneur) joigne le service, pas le Wi-Fi.
HOST = os.environ.get("SENTINEL_VISION_HOST", "127.0.0.1")
PORT = int(os.environ.get("SENTINEL_VISION_PORT", "8081"))
CAMERA_INDEX = int(os.environ.get("SENTINEL_CAMERA_INDEX", "0"))
# Chemin stable de la camera (ex. /dev/v4l/by-id/...). Prioritaire sur l'index si defini.
CAMERA_DEVICE = os.environ.get("SENTINEL_CAMERA_DEVICE", "")
CAPTURE_W = int(os.environ.get("SENTINEL_CAPTURE_WIDTH", "640"))
CAPTURE_H = int(os.environ.get("SENTINEL_CAPTURE_HEIGHT", "480"))
# Hauteur max du flux (0 = pas de redimensionnement), ex. 440 pour du 440p
OUTPUT_HEIGHT = int(os.environ.get("SENTINEL_OUTPUT_HEIGHT", "0"))
# Correction contre-jour : reglages camera + eclaircissement local des ombres (CLAHE)
CAM_BACKLIGHT = os.environ.get("SENTINEL_CAM_BACKLIGHT", "")  # 0-6 sur la UGREEN
CAM_GAMMA = os.environ.get("SENTINEL_CAM_GAMMA", "")          # 72-500
CAM_BRIGHTNESS = os.environ.get("SENTINEL_CAM_BRIGHTNESS", "")  # -64..64
SHADOW_LIFT = float(os.environ.get("SENTINEL_SHADOW_LIFT", "0"))  # 0 = off, ~2.0 = moyen
_clahe = cv2.createCLAHE(clipLimit=SHADOW_LIFT, tileGridSize=(8, 8)) if SHADOW_LIFT > 0 else None


def lift_shadows(frame):
    if _clahe is None:
        return frame
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
API_ALERTS_URL = os.environ.get(
    "SENTINEL_API_ALERTS",
    os.environ.get("API_BASE_URL", "http://localhost:3000").rstrip("/") + "/api/v1/alerts",
)
API_TOKEN = (os.environ.get("SENTINEL_API_TOKEN") or os.environ.get("API_TOKEN") or "").strip()
DEVICE_ID = os.environ.get("SENTINEL_CAM_DEVICE_ID", "sentinel-cam-01")
MODEL_NAME = os.environ.get("SENTINEL_YOLO_MODEL", "yolov8n.pt")
INFER_INTERVAL = float(os.environ.get("SENTINEL_INFER_INTERVAL", "0.12"))  # ~8 FPS
STREAM_FPS = float(os.environ.get("SENTINEL_STREAM_FPS", "15"))
JPEG_QUALITY = int(os.environ.get("SENTINEL_JPEG_QUALITY", "70"))
DEBOUNCE_S = float(os.environ.get("SENTINEL_PERSON_DEBOUNCE", "2.0"))
COOLDOWN_S = float(os.environ.get("SENTINEL_PERSON_COOLDOWN", "10.0"))
PERSON_CLASS_ID = 0  # COCO person
# Similarité cosinus SFace. Référence OpenCV : 0,363 (plus haut = plus strict).
FACE_THRESHOLD = float(os.environ.get("SENTINEL_FACE_THRESHOLD", "0.363"))
FACE_DEBOUNCE = float(os.environ.get("SENTINEL_FACE_DEBOUNCE", "1.5"))
FACE_INTERVAL = float(os.environ.get("SENTINEL_FACE_INTERVAL", "0.45"))
_VISION_DIR = Path(__file__).resolve().parent
FACE_DIR = Path(os.environ.get("SENTINEL_FACE_DIR", str(_VISION_DIR / "data" / "gallery")))
FACE_MODEL_DIR = Path(os.environ.get("SENTINEL_FACE_MODEL_DIR", str(_VISION_DIR / "models")))


class CameraWorker:
    """Single shared camera reader + YOLO inference thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._fps = 0.0
        self._infer_fps = 0.0
        self._infer_ms = 0.0       # temps d'inference YOLO moyen (30 dernieres trames), ms
        self._infer_ms_last = 0.0
        self._infer_ms_max = 0.0
        self._infer_shape = (0, 0)
        self._persons = 0
        self._camera_ok = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model: YOLO | None = None
        self._last_boxes: list[tuple[int, int, int, int, float, str]] = []
        self._raw_frame = None  # derniere trame brute (enrolement par capture)
        self._person_state = False  # currently considered "person present"
        self._person_since: float | None = None  # when count first became >0 or ==0
        self._pending_present: bool | None = None
        self._last_alert_ts = 0.0
        self._started_at = time.monotonic()
        self._gallery = Gallery(FACE_DIR)
        self._face_engine = FaceEngine(FACE_MODEL_DIR)
        self._face_live: dict[str, Any] = {
            "status": "unavailable" if not self._face_engine.ready else "none",
            "name": None,
            "score": None,
            "faces": 0,
        }
        self._face_marks: list[tuple[int, int, int, int, str, bool]] = []
        self._face_pending: str | None = None
        self._face_pending_name: str | None = None
        self._face_since: float | None = None
        self._face_reported: str | None = None
        self._face_reported_name: str | None = None
        if self._face_engine.ready:
            logger.info("Reconnaissance visage prête (%s), seuil %.3f", self._face_engine.detail, FACE_THRESHOLD)
        else:
            logger.warning("Reconnaissance visage indisponible : %s", self._face_engine.detail)

    def start(self) -> None:
        logger.info("Loading YOLO model %s (CPU)…", MODEL_NAME)
        self._model = YOLO(MODEL_NAME)
        self._thread = threading.Thread(target=self._run, name="cam-yolo", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "camera_ok": self._camera_ok,
                "fps": round(self._fps, 1),
                "infer_fps": round(self._infer_fps, 1),
                "infer_ms": round(self._infer_ms, 1),
                "infer_ms_last": round(self._infer_ms_last, 1),
                "infer_ms_max": round(self._infer_ms_max, 1),
                "infer_frame": list(self._infer_shape),
                "persons": self._persons,
                "face_ready": self._face_engine.ready,
                "face_status": self._face_live["status"],
                "face_name": self._face_live["name"],
                "face_score": None if self._face_live["score"] is None else round(float(self._face_live["score"]), 3),
                "face_threshold": FACE_THRESHOLD,
                "face_count": self._face_live["faces"],
                "face_gallery": self._gallery.count(),
                "face_model": self._face_engine.detail,
                "model": MODEL_NAME,
                "device": "cpu",
                "uptime_s": round(time.monotonic() - self._started_at, 1),
            }

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def get_raw_jpeg(self) -> bytes | None:
        """Trame brute (sans cadres ni bandeau), pour l'enrôlement par capture."""
        with self._lock:
            frame = self._raw_frame
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        return buf.tobytes() if ok else None

    def _open_camera(self) -> cv2.VideoCapture | None:
        src = os.path.realpath(CAMERA_DEVICE) if CAMERA_DEVICE else CAMERA_INDEX
        cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_H)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for prop, val in ((cv2.CAP_PROP_BACKLIGHT, CAM_BACKLIGHT), (cv2.CAP_PROP_GAMMA, CAM_GAMMA),
                          (cv2.CAP_PROP_BRIGHTNESS, CAM_BRIGHTNESS)):
            if val != "":
                cap.set(prop, float(val))
        logger.info("Camera reglages: backlight=%s gamma=%s brightness=%s shadow_lift=%s",
                    cap.get(cv2.CAP_PROP_BACKLIGHT), cap.get(cv2.CAP_PROP_GAMMA),
                    cap.get(cv2.CAP_PROP_BRIGHTNESS), SHADOW_LIFT)
        logger.info("Camera %s: capture %dx%d", src,
                    int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        return cap

    def _post_alert(self, state: str, persons: int, conf: float | None = None) -> None:
        body: dict[str, Any] = {
            "device_id": DEVICE_ID,
            "type": "vision",
            "state": state,
            "uptime_ms": int((time.monotonic() - self._started_at) * 1000),
        }
        # Schema only has device_id/type/state/uptime_ms — keep extra out of body.
        # Confidence/count logged locally; optional raw note via state suffix not needed.
        logger.info(
            "Posting alert %s (persons=%s conf=%s) → %s",
            state,
            persons,
            conf,
            API_ALERTS_URL,
        )
        try:
            data = json.dumps(body).encode("utf-8")
            req = Request(
                API_ALERTS_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}),
                },
                method="POST",
            )
            with urlopen(req, timeout=3) as resp:
                logger.info("Alert OK status=%s", resp.status)
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("Alert POST failed (ignored): %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alert POST unexpected error (ignored): %s", exc)

    def _update_person_alerts(self, persons: int, max_conf: float) -> None:
        now = time.monotonic()
        present = persons >= 1
        if self._pending_present is None or present != self._pending_present:
            self._pending_present = present
            self._person_since = now
            return
        if self._person_since is None:
            return
        if now - self._person_since < DEBOUNCE_S:
            return
        if present == self._person_state:
            return
        if now - self._last_alert_ts < COOLDOWN_S:
            return
        self._person_state = present
        self._last_alert_ts = now
        state = "person_detected" if present else "person_cleared"
        self._post_alert(state, persons, max_conf if present else None)

    def face_public(self) -> dict[str, Any]:
        with self._lock:
            live = dict(self._face_live)
            ready = self._face_engine.ready
        score = live.get("score")
        return {
            "face_ready": ready,
            "status": live.get("status") or "unavailable",
            "name": live.get("name"),
            "score": None if score is None else round(float(score), 3),
            "threshold": FACE_THRESHOLD,
            "faces": live.get("faces") or 0,
            "gallery": self._gallery.count(),
            "model": self._face_engine.detail,
        }

    def _recognize(self, frame: np.ndarray) -> None:
        engine = self._face_engine
        if not engine.ready:
            with self._lock:
                self._face_live = {"status": "unavailable", "name": None, "score": None, "faces": 0}
                self._face_marks = []
            return
        try:
            detections = engine.detect(frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconnaissance visage: %s", exc)
            return
        marks: list[tuple[int, int, int, int, str, bool]] = []
        known_names: list[str] = []
        any_unknown = False
        best_unknown: float | None = None
        worst_known: float | None = None
        for det in detections:
            hit = self._gallery.match(det.embedding, FACE_THRESHOLD)
            if hit.status == "known" and hit.name:
                known_names.append(hit.name)
                label = f"{hit.name} {hit.score:.2f}" if hit.score is not None else hit.name
                marks.append((*det.box, label, True))
                if hit.score is not None:
                    worst_known = hit.score if worst_known is None else min(worst_known, hit.score)
            else:
                any_unknown = True
                label = f"inconnu {hit.score:.2f}" if hit.score is not None else "inconnu"
                marks.append((*det.box, label, False))
                if hit.score is not None:
                    best_unknown = hit.score if best_unknown is None else max(best_unknown, hit.score)
        if not detections:
            status, name, score = "none", None, None
        elif any_unknown:
            status, name, score = "unknown", None, best_unknown
        else:
            status, name, score = "known", ", ".join(dict.fromkeys(known_names)), worst_known
        with self._lock:
            self._face_live = {"status": status, "name": name, "score": score, "faces": len(detections)}
            self._face_marks = marks
        self._update_face_alerts(status, name)

    def _note_face_absent(self) -> None:
        """Caméra coupée : plus de visage, pour laisser retomber l'alarme inconnue."""
        if not self._face_engine.ready:
            return
        with self._lock:
            self._face_live = {"status": "none", "name": None, "score": None, "faces": 0}
            self._face_marks = []
        self._update_face_alerts("none", None)

    def _update_face_alerts(self, status: str, name: str | None) -> None:
        now = time.monotonic()
        if status != self._face_pending or name != self._face_pending_name:
            self._face_pending = status
            self._face_pending_name = name
            self._face_since = now
            return
        if self._face_since is None or now - self._face_since < FACE_DEBOUNCE:
            return
        if status == self._face_reported and name == self._face_reported_name:
            return
        # Pas d'alerte « plus de visage » au démarrage, tant qu'aucun état n'a été publié.
        if status == "none" and self._face_reported is None:
            self._face_reported = status
            self._face_reported_name = name
            return
        self._face_reported = status
        self._face_reported_name = name
        logger.info("Visage stable %s name=%s", status, name)
        if status == "unknown":
            self._post_alert("face_unknown", self._persons, None)
        elif status == "known":
            self._post_alert("face_known", self._persons, None)
        else:
            self._post_alert("face_cleared", self._persons, None)

    def _draw_overlay(
        self,
        frame: np.ndarray,
        boxes: list[tuple[int, int, int, int, float, str]],
        persons: int,
        fps: float,
        face_marks: list[tuple[int, int, int, int, str, bool]] | None = None,
    ) -> np.ndarray:
        out = frame
        for x1, y1, x2, y2, conf, label in boxes:
            cv2.rectangle(out, (x1, y1), (x2, y2), (59, 224, 192), 2)
            tag = f"{label} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(out, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), (59, 224, 192), -1)
            cv2.putText(
                out,
                tag,
                (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (10, 20, 30),
                1,
                cv2.LINE_AA,
            )
        for x, y, bw, bh, label, known in face_marks or []:
            color = (74, 125, 47) if known else (38, 92, 196)
            cv2.rectangle(out, (x, y), (x + bw, y + bh), color, 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            y0 = y + bh + th + 6
            if y0 > out.shape[0]:
                y0 = max(th + 4, y - 4)
            cv2.rectangle(out, (x, y0 - th - 4), (x + tw + 4, y0 + 2), color, -1)
            cv2.putText(out, label, (x + 2, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (236, 241, 242), 1, cv2.LINE_AA)
        banner = f"SENTINEL-X IA  |  {fps:.1f} FPS  |  inference {self._infer_ms:.0f} ms  |  personnes: {persons}"
        cv2.rectangle(out, (0, 0), (out.shape[1], 28), (12, 18, 32), -1)
        cv2.putText(
            out,
            banner,
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (59, 224, 192),
            1,
            cv2.LINE_AA,
        )
        return out

    def _run(self) -> None:
        assert self._model is not None
        cap: cv2.VideoCapture | None = None
        last_infer = 0.0
        last_face = 0.0
        frame_times: list[float] = []
        infer_times: list[float] = []
        stream_interval = 1.0 / max(STREAM_FPS, 1.0)
        last_encode = 0.0
        reconnect_delay = 1.0

        while not self._stop.is_set():
            try:
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    logger.info("Opening camera %s…", CAMERA_DEVICE or CAMERA_INDEX)
                    cap = self._open_camera()
                    if cap is None:
                        with self._lock:
                            self._camera_ok = False
                            self._jpeg = self._placeholder_jpeg("Camera offline")
                        self._note_face_absent()
                        time.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 1.5, 10.0)
                        continue
                    reconnect_delay = 1.0
                    logger.info("Camera opened")

                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Camera read failed — reconnecting")
                    with self._lock:
                        self._camera_ok = False
                    self._note_face_absent()
                    cap.release()
                    cap = None
                    time.sleep(0.5)
                    continue

                if OUTPUT_HEIGHT and frame.shape[0] > OUTPUT_HEIGHT:
                    h, w = frame.shape[:2]
                    nw = int(round(w * OUTPUT_HEIGHT / h / 2)) * 2
                    frame = cv2.resize(frame, (nw, OUTPUT_HEIGHT), interpolation=cv2.INTER_AREA)

                frame = lift_shadows(frame)

                now = time.monotonic()
                with self._lock:
                    self._camera_ok = True
                    self._raw_frame = frame

                if now - last_infer >= INFER_INTERVAL:
                    t0 = time.monotonic()
                    results = self._model.predict(
                        frame, verbose=False, device="cpu", classes=[PERSON_CLASS_ID], conf=0.45
                    )
                    boxes: list[tuple[int, int, int, int, float, str]] = []
                    max_conf = 0.0
                    for r in results:
                        if r.boxes is None:
                            continue
                        for b in r.boxes:
                            conf = float(b.conf[0])
                            xyxy = b.xyxy[0].tolist()
                            x1, y1, x2, y2 = (int(v) for v in xyxy)
                            boxes.append((x1, y1, x2, y2, conf, "person"))
                            max_conf = max(max_conf, conf)
                    persons = len(boxes)
                    self._last_boxes = boxes
                    last_infer = now
                    dt = time.monotonic() - t0
                    infer_times.append(dt)
                    if len(infer_times) > 30:
                        infer_times.pop(0)
                    if infer_times:
                        avg = sum(infer_times) / len(infer_times)
                        with self._lock:
                            self._infer_fps = 1.0 / avg if avg > 0 else 0.0
                            self._infer_ms = avg * 1000.0
                            self._infer_ms_last = dt * 1000.0
                            self._infer_ms_max = max(infer_times) * 1000.0
                            self._infer_shape = (frame.shape[1], frame.shape[0])
                            self._persons = persons
                    self._update_person_alerts(persons, max_conf)
                    if now - last_face >= FACE_INTERVAL:
                        self._recognize(frame)
                        last_face = time.monotonic()

                annotated = self._draw_overlay(
                    frame.copy(), self._last_boxes, len(self._last_boxes), self._fps, self._face_marks
                )

                if now - last_encode >= stream_interval:
                    ok_enc, buf = cv2.imencode(
                        ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                    )
                    if ok_enc:
                        with self._lock:
                            self._jpeg = buf.tobytes()
                        last_encode = now
                        frame_times.append(now)
                        cutoff = now - 2.0
                        frame_times = [t for t in frame_times if t >= cutoff]
                        if len(frame_times) >= 2:
                            span = frame_times[-1] - frame_times[0]
                            with self._lock:
                                self._fps = (len(frame_times) - 1) / span if span > 0 else 0.0

                # Pace the capture loop (~stream FPS) so we don't busy-spin
                elapsed = time.monotonic() - now
                time.sleep(max(0.0, stream_interval - elapsed))

            except Exception as exc:  # noqa: BLE001
                logger.exception("Vision loop error: %s", exc)
                with self._lock:
                    self._camera_ok = False
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                time.sleep(1.0)

        if cap is not None:
            cap.release()

    @staticmethod
    def _placeholder_jpeg(text: str) -> bytes:
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (18, 24, 40)
        cv2.putText(
            img,
            text,
            (40, 180),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (59, 224, 192),
            2,
            cv2.LINE_AA,
        )
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return buf.tobytes() if ok else b""


WORKER = CameraWorker()


class VisionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # pas de bannière "BaseHTTP/x Python/x.y" (reconnaissance de version)
    server_version = "sentinel-vision"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("HTTP " + fmt, *args)

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/health/"):
            self._send_json(200, WORKER.snapshot())
            return
        if path in ("/snapshot.jpg", "/snapshot"):
            jpeg = WORKER.get_jpeg()
            if not jpeg:
                self._send_json(503, {"error": "no frame yet"})
                return
            self._send_bytes(200, jpeg, "image/jpeg")
            return
        if path == "/snapshot_raw.jpg":
            jpeg = WORKER.get_raw_jpeg()
            if not jpeg:
                self._send_json(503, {"error": "no frame yet"})
                return
            self._send_bytes(200, jpeg, "image/jpeg")
            return
        if path in ("/stream.mjpg", "/stream", "/"):
            self._stream_mjpeg()
            return
        if path == "/faces/status":
            if not self._bearer_ok():
                self._send_json(401, {"error": "authentification requise"})
                return
            self._send_json(200, WORKER.face_public())
            return
        if path == "/faces":
            if not self._bearer_ok():
                self._send_json(401, {"error": "authentification requise"})
                return
            self._send_json(200, {"people": WORKER._gallery.list_people()})  # noqa: SLF001
            return
        if path.startswith("/faces/") and path.endswith("/thumb"):
            if not self._bearer_ok():
                self._send_json(401, {"error": "authentification requise"})
                return
            person_id = path[len("/faces/") : -len("/thumb")].strip("/")
            thumb = WORKER._gallery.thumb_file(person_id)  # noqa: SLF001
            if thumb is None:
                self._send_json(404, {"error": "vignette introuvable"})
                return
            self._send_bytes(200, thumb.read_bytes(), "image/jpeg")
            return
        if path.startswith("/faces/") and path.endswith("/portrait"):
            if not self._bearer_ok():
                self._send_json(401, {"error": "authentification requise"})
                return
            person_id = path[len("/faces/") : -len("/portrait")].strip("/")
            portrait = WORKER._gallery.portrait_file(person_id)  # noqa: SLF001
            if portrait is None:
                self._send_json(404, {"error": "portrait introuvable"})
                return
            self._send_bytes(200, portrait.read_bytes(), "image/jpeg")
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        portrait_for = None
        if path.startswith("/faces/") and path.endswith("/portrait"):
            portrait_for = path[len("/faces/") : -len("/portrait")].strip("/")
            if not portrait_for or "/" in portrait_for:
                self._send_json(404, {"error": "not found"})
                return
        elif path not in ("/faces", "/faces/"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._bearer_ok():
            self._send_json(401, {"error": "authentification requise"})
            return
        body = self._read_body()
        if body is None:
            return
        try:
            name, photos, opts = parse_multipart_opts(self.headers.get("Content-Type", ""), body)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        if portrait_for is not None:
            code, payload = set_portrait_image(WORKER._gallery, WORKER._face_engine, portrait_for, photos)  # noqa: SLF001
            self._send_json(code, payload)
            return
        single = opts.get("single", "") in ("1", "true", "yes")
        code, payload = enroll_images(WORKER._gallery, WORKER._face_engine, name, photos, single=single)  # noqa: SLF001
        self._send_json(code, payload)

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not path.startswith("/faces/"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._bearer_ok():
            self._send_json(401, {"error": "authentification requise"})
            return
        person_id = path[len("/faces/") :].strip("/")
        if "/" in person_id or not WORKER._gallery.delete(person_id):  # noqa: SLF001
            self._send_json(404, {"error": "personne introuvable"})
            return
        self._send_json(200, {"ok": True})

    def _bearer_ok(self) -> bool:
        if not API_TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return False
        got = header[7:].strip()
        if len(got) != len(API_TOKEN):
            return False
        return hmac.compare_digest(got, API_TOKEN)

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_json(400, {"error": "longueur invalide"})
            return None
        if length < 0 or length > 12_000_000:
            self._send_json(413, {"error": "photo trop volumineuse"})
            return None
        return self.rfile.read(length) if length else b""

    def _stream_mjpeg(self) -> None:
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.end_headers()
        boundary = b"--frame\r\n"
        try:
            while not WORKER._stop.is_set():  # noqa: SLF001
                jpeg = WORKER.get_jpeg()
                if jpeg:
                    header = (
                        boundary
                        + b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(header)
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(1.0 / max(STREAM_FPS, 1.0))
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def main() -> None:
    WORKER.start()
    # Wait briefly for first frame
    for _ in range(50):
        if WORKER.get_jpeg():
            break
        time.sleep(0.1)
    server = ThreadingHTTPServer((HOST, PORT), VisionHandler)
    logger.info("MJPEG listening on http://%s:%s/stream.mjpg", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down…")
    finally:
        WORKER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
