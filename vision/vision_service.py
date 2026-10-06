#!/usr/bin/env python3
"""SENTINEL-X vision service: webcam + YOLO → MJPEG + API alerts."""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from discord_alert import DiscordAlerter
from face_api import enroll_images, parse_multipart_opts, set_portrait_image
from face_engine import FaceEngine
from face_gallery import Gallery
from face_liveness import LivenessConfig, LivenessTracker, aggregate_status, face_verdict
from identity import AUTHORIZED, IDENTIFYING, INTRUSION, IdentityConfig, IdentityMachine

CAPTURE_DELAY_S = float(os.environ.get("SENTINEL_CAPTURE_DELAY_S", "1.0"))  # capture Discord après détection

# Cap CPU threads BEFORE loading torch/ultralytics
_nt = int(os.environ.get("TORCH_NUM_THREADS", "2"))
os.environ.setdefault("OMP_NUM_THREADS", str(_nt))
os.environ.setdefault("MKL_NUM_THREADS", str(_nt))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_nt))
# threads OpenMP en attente passive : sans cela ils tournent à vide entre deux inférences
# (mesuré : YOLO à 5 Hz ~150 % CPU -> ~75 %, pour ~+9 ms d'inférence, toujours < 100 ms)
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

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
CAPTURE_FPS = float(os.environ.get("SENTINEL_CAPTURE_FPS", "30"))
# Tampons V4L2 : avec 1 seul tampon la UGREEN perd une trame sur deux (15 FPS) ; avec 4 elle
# tient 30 FPS. Le fil de capture lit en continu, donc la latence reste d'environ une trame.
CAPTURE_BUFFERS = int(os.environ.get("SENTINEL_CAPTURE_BUFFERS", "4"))
# Hauteur max du flux diffusé (0 = résolution de capture), ex. 720
OUTPUT_HEIGHT = int(os.environ.get("SENTINEL_OUTPUT_HEIGHT", "0"))
# Hauteur de la trame d'inférence (YOLO + visages), découplée du flux : 440 = trame historique
# 782x440 (YOLO la ramène de toute façon à 640 px), pour ne pas changer la reconnaissance.
INFER_HEIGHT = int(os.environ.get("SENTINEL_INFER_HEIGHT", "440"))
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
STREAM_FPS = float(os.environ.get("SENTINEL_STREAM_FPS", "30"))
JPEG_QUALITY = int(os.environ.get("SENTINEL_JPEG_QUALITY", "80"))
STREAM_MAX_CLIENTS = int(os.environ.get("SENTINEL_STREAM_MAX_CLIENTS", "6"))
DEBOUNCE_S = float(os.environ.get("SENTINEL_PERSON_DEBOUNCE", "2.0"))
COOLDOWN_S = float(os.environ.get("SENTINEL_PERSON_COOLDOWN", "10.0"))
PERSON_CLASS_ID = 0  # COCO person
# Similarité cosinus SFace. Référence OpenCV : 0,363 (plus haut = plus strict).
FACE_THRESHOLD = float(os.environ.get("SENTINEL_FACE_THRESHOLD", "0.363"))
FACE_DEBOUNCE = float(os.environ.get("SENTINEL_FACE_DEBOUNCE", "1.5"))
FACE_INTERVAL = float(os.environ.get("SENTINEL_FACE_INTERVAL", "0.30"))
# Anti-spoofing : trame mesurée = "raw" (avant éclaircissement CLAHE) ou "processed"
LIVENESS_SOURCE = os.environ.get("SENTINEL_LIVENESS_SOURCE", "raw").strip().lower()
LIVE_CFG = LivenessConfig.from_env()
IDENT_CFG = IdentityConfig.from_env()
# 1 = journalise chaque mesure (score sur trame brute ET éclaircie) pour calibrer sur site
LIVE_DEBUG = os.environ.get("SENTINEL_LIVENESS_DEBUG", "0") == "1"
_VISION_DIR = Path(__file__).resolve().parent
FACE_DIR = Path(os.environ.get("SENTINEL_FACE_DIR", str(_VISION_DIR / "data" / "gallery")))
FACE_MODEL_DIR = Path(os.environ.get("SENTINEL_FACE_MODEL_DIR", str(_VISION_DIR / "models")))


def _resize_h(frame: np.ndarray, height: int) -> np.ndarray:
    """Réduit la trame à `height` px de haut (largeur paire), jamais d'agrandissement."""
    if not height or frame.shape[0] <= height:
        return frame
    h, w = frame.shape[:2]
    nw = int(round(w * height / h / 2)) * 2
    return cv2.resize(frame, (nw, height), interpolation=cv2.INTER_AREA)


class CameraWorker:
    """Caméra découplée de l'IA.

    - fil « cam-capture » : lit la webcam à sa cadence (30 FPS), garde la dernière trame ;
    - fil « cam-infer » : YOLO + visages sur la dernière trame, réduite à INFER_HEIGHT,
      au rythme permis (INFER_INTERVAL / FACE_INTERVAL) ;
    - encodage JPEG à la demande (flux, snapshot, Discord) : une fois par trame, partagé entre
      les clients, avec les dernières détections incrustées. Personne ne regarde = pas d'encodage.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame_cond = threading.Condition()   # nouvelle trame capturée
        self._frame: np.ndarray | None = None
        self._frame_seq = 0
        self._enc_lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._jpeg_seq = -1
        self._offline_jpeg = self._placeholder_jpeg("Camera offline")
        self._capture_shape = (0, 0)
        self._stream_times: list[float] = []
        self._stream_clients = 0
        self._infer_runs: list[float] = []
        self._fps = 0.0
        self._infer_fps = 0.0
        self._infer_ms = 0.0       # temps d'inference YOLO moyen (30 dernieres trames), ms
        self._infer_ms_last = 0.0
        self._infer_ms_max = 0.0
        self._infer_shape = (0, 0)
        self._persons = 0
        self._camera_ok = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._model: YOLO | None = None
        self._last_boxes: list[tuple[int, int, int, int, float, str]] = []
        self._raw_frame = None  # derniere trame brute (enrolement par capture)
        self._person_state = False  # currently considered "person present"
        self._person_since: float | None = None  # when count first became >0 or ==0
        self._pending_present: bool | None = None
        self._last_alert_ts = 0.0
        self._started_at = time.monotonic()
        self._gallery = Gallery(FACE_DIR)
        self._face_engine = FaceEngine(FACE_MODEL_DIR, LIVE_CFG)
        self._face_live: dict[str, Any] = {
            "status": "unavailable" if not self._face_engine.ready else "none",
            "name": None,
            "score": None,
            "faces": 0,
            "liveness": None,
            "live_state": None,
        }
        self._face_marks: list[tuple[int, int, int, int, str, str]] = []
        self._tracker = LivenessTracker(LIVE_CFG)
        self._live_ms: list[float] = []
        self._discord = DiscordAlerter(self.get_jpeg, camera=DEVICE_ID)
        self._capture_jpeg: bytes | None = None  # capture prise ~1 s après la détection
        self._capture_due: float | None = None
        self._identity = IdentityMachine(IDENT_CFG)
        self._face_pending: str | None = None
        self._face_pending_name: str | None = None
        self._face_since: float | None = None
        self._face_reported: str | None = None
        self._face_reported_name: str | None = None
        if self._face_engine.ready:
            logger.info("Reconnaissance visage prête (%s), seuil %.3f", self._face_engine.detail, FACE_THRESHOLD)
            if self._face_engine.liveness_on:
                logger.info(
                    "Anti-spoofing prêt (%s) : vivant>=%.2f leurre<=%.2f, %d mesures, visage>=%dpx, trame %s",
                    self._face_engine.live.detail, LIVE_CFG.live_threshold, LIVE_CFG.spoof_threshold,
                    LIVE_CFG.min_obs, LIVE_CFG.min_face, LIVENESS_SOURCE,
                )
            else:
                logger.warning("Anti-spoofing inactif : %s", self._face_engine.live.detail)
        else:
            logger.warning("Reconnaissance visage indisponible : %s", self._face_engine.detail)
        logger.info("Alertes Discord : %s", "actives" if self._discord.enabled else "inactives")
        logger.info("Identification : %s (fenêtre %.0f s, grâce %.0f s, repos %.0f s)",
                    "active" if self.identify_on else "inactive", IDENT_CFG.window_s,
                    IDENT_CFG.auth_grace_s, IDENT_CFG.idle_s)

    @property
    def identify_on(self) -> bool:
        return IDENT_CFG.enabled and self._face_engine.ready

    def start(self) -> None:
        logger.info("Loading YOLO model %s (CPU)…", MODEL_NAME)
        self._model = YOLO(MODEL_NAME)
        self._threads = [
            threading.Thread(target=self._capture_loop, name="cam-capture", daemon=True),
            threading.Thread(target=self._infer_loop, name="cam-infer", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        with self._frame_cond:
            self._frame_cond.notify_all()
        for t in self._threads:
            t.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            stream = [t for t in self._stream_times if t >= now - 2.0]
            runs = [t for t in self._infer_runs if t >= now - 5.0]
            return {
                "camera_ok": self._camera_ok,
                "fps": round(self._fps, 1),                 # cadence de capture
                "capture_fps": round(self._fps, 1),
                "capture_frame": list(self._capture_shape),
                "stream_fps": round((len(stream) - 1) / (stream[-1] - stream[0]), 1)
                if len(stream) >= 2 and stream[-1] > stream[0] else 0.0,
                "stream_clients": self._stream_clients,
                "stream_quality": JPEG_QUALITY,
                "infer_rate": round(len(runs) / 5.0, 1),    # inférences YOLO réellement faites / s
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
                "face_liveness": self._face_live.get("liveness"),
                "face_live_state": self._face_live.get("live_state"),
                "liveness_ready": self._face_engine.liveness_on,
                "liveness_ms": round(sum(self._live_ms) / len(self._live_ms), 2) if self._live_ms else None,
                "face_interval_s": FACE_INTERVAL,
                "discord_alerts": self._discord.enabled,
                "identify_enabled": self.identify_on,
                "identity": self._identity.snapshot(time.monotonic()),
                "model": MODEL_NAME,
                "device": "cpu",
                "uptime_s": round(time.monotonic() - self._started_at, 1),
            }

    def get_jpeg(self) -> bytes | None:
        """Dernière trame annotée en JPEG, encodée au plus une fois par trame capturée."""
        with self._enc_lock:
            with self._lock:
                camera_ok = self._camera_ok
            if not camera_ok:
                return self._offline_jpeg
            with self._frame_cond:
                frame, seq = self._frame, self._frame_seq
            if frame is None:
                return None
            if seq == self._jpeg_seq and self._jpeg:
                return self._jpeg
            out = lift_shadows(_resize_h(frame, OUTPUT_HEIGHT))
            if out is frame:
                out = frame.copy()  # ne jamais dessiner sur la trame partagée
            with self._lock:
                infer_h = self._infer_shape[1]
                boxes, marks, persons, fps = list(self._last_boxes), list(self._face_marks), len(self._last_boxes), self._fps
            scale = out.shape[0] / infer_h if infer_h else 1.0
            self._draw_overlay(out, boxes, persons, fps, marks, scale)
            ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            if not ok:
                return self._jpeg
            self._jpeg, self._jpeg_seq = buf.tobytes(), seq
            now = time.monotonic()
            with self._lock:
                self._stream_times = [t for t in self._stream_times if t >= now - 2.0] + [now]
            return self._jpeg

    def wait_frame(self, last_seq: int, timeout: float) -> int:
        """Bloque jusqu'à une trame plus récente que last_seq (ou timeout) ; renvoie son numéro."""
        with self._frame_cond:
            self._frame_cond.wait_for(lambda: self._frame_seq != last_seq or self._stop.is_set(), timeout)
            return self._frame_seq

    def stream_slot(self, delta: int) -> bool:
        with self._lock:
            if delta > 0 and self._stream_clients >= STREAM_MAX_CLIENTS:
                return False
            self._stream_clients += delta
            return True

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
        cap.set(cv2.CAP_PROP_FPS, CAPTURE_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, CAPTURE_BUFFERS)
        for prop, val in ((cv2.CAP_PROP_BACKLIGHT, CAM_BACKLIGHT), (cv2.CAP_PROP_GAMMA, CAM_GAMMA),
                          (cv2.CAP_PROP_BRIGHTNESS, CAM_BRIGHTNESS)):
            if val != "":
                cap.set(prop, float(val))
        logger.info("Camera reglages: backlight=%s gamma=%s brightness=%s shadow_lift=%s",
                    cap.get(cv2.CAP_PROP_BACKLIGHT), cap.get(cv2.CAP_PROP_GAMMA),
                    cap.get(cv2.CAP_PROP_BRIGHTNESS), SHADOW_LIFT)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        logger.info("Camera %s: capture %dx%d %s @ %.0f FPS demandés", src,
                    int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)), cap.get(cv2.CAP_PROP_FPS))
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
            "identify_enabled": self.identify_on,
            "identity": self._identity.snapshot(time.monotonic()),
            "liveness": {
                "ready": self._face_engine.liveness_on,
                "state": live.get("live_state"),
                "score": live.get("liveness"),
                "live_threshold": LIVE_CFG.live_threshold,
                "spoof_threshold": LIVE_CFG.spoof_threshold,
                "min_obs": LIVE_CFG.min_obs,
                "model": self._face_engine.live.detail,
            },
        }

    def _recognize(self, frame: np.ndarray, live_frame: np.ndarray | None = None) -> None:
        engine = self._face_engine
        if not engine.ready:
            with self._lock:
                self._face_live = {"status": "unavailable", "name": None, "score": None, "faces": 0,
                                   "liveness": None, "live_state": None}
                self._face_marks = []
            return
        try:
            detections = engine.detect(frame)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconnaissance visage: %s", exc)
            return
        now = time.monotonic()
        live_on = engine.liveness_on
        src = live_frame if (live_frame is not None and LIVENESS_SOURCE == "raw") else frame
        tracks = self._tracker.assign([d.box for d in detections], now)
        marks: list[tuple[int, int, int, int, str, str]] = []
        rows: list[tuple[str, str | None, float | None, float | None, str]] = []
        for det, tr in zip(detections, tracks):
            if live_on and self._tracker.wants_measure(tr):
                t0 = time.perf_counter()
                probs = engine.liveness_probs(src, det.box)
                if probs is not None:
                    tr.observe(float(probs[1]), LIVE_CFG)
                    self._live_ms = (self._live_ms + [(time.perf_counter() - t0) * 1000.0])[-30:]
                    if LIVE_DEBUG:
                        other = frame if src is not frame else live_frame
                        alt = engine.liveness_probs(other, det.box) if other is not None else None
                        logger.info("liveness piste=%d w=%d %s=%.3f autre=%s ema=%.3f etat=%s probs=%s",
                                    tr.track_id, det.box[2], LIVENESS_SOURCE, probs[1],
                                    "-" if alt is None else f"{alt[1]:.3f}", tr.ema, tr.state,
                                    ",".join(f"{v:.2f}" for v in probs))
            hit = self._gallery.match(det.embedding, FACE_THRESHOLD)
            known = hit.status == "known" and bool(hit.name)
            verdict = face_verdict(known, "checking" if tr.small else tr.state, live_on)
            rows.append((verdict, hit.name if known else None, hit.score, tr.ema if live_on else None, tr.state))
            marks.append((*det.box, self._face_label(verdict, hit.name if known else None, hit.score,
                                                     tr.ema if live_on else None, tr.small), verdict))
        status = aggregate_status([r[0] for r in rows])
        name, score, liveness, live_state = None, None, None, None
        if rows:
            chosen = [r for r in rows if r[0] == status]
            scores = [r[2] for r in rows if r[2] is not None]
            lives = [r[3] for r in chosen if r[3] is not None]
            if status == "known":
                name = ", ".join(dict.fromkeys(r[1] for r in chosen if r[1]))
                known_scores = [r[2] for r in chosen if r[2] is not None]
                score = min(known_scores) if known_scores else None
            elif status == "checking":
                name = ", ".join(dict.fromkeys(r[1] for r in chosen if r[1])) or None
                score = max(scores) if scores else None
            else:
                own = [r[2] for r in chosen if r[2] is not None]
                score = max(own) if own else None
            liveness = min(lives) if lives else None
            if live_on:
                live_state = {"spoof": "spoof", "known": "live"}.get(status) or (
                    "live" if status == "unknown" and liveness is not None and liveness >= LIVE_CFG.live_threshold
                    else "checking")
        with self._lock:
            self._face_live = {"status": status, "name": name, "score": score, "faces": len(detections),
                               "liveness": None if liveness is None else round(float(liveness), 3),
                               "live_state": live_state}
            self._face_marks = marks
        if self.identify_on:
            # portillon : un seul visage connu ET vivant suffit à s'identifier, même si un visage
            # inconnu (arrière-plan) est aussi dans le champ
            known_rows = [r for r in rows if r[0] == "known"]
            if known_rows:
                kname = ", ".join(dict.fromkeys(r[1] for r in known_rows if r[1]))
                klive = [r[3] for r in known_rows if r[3] is not None]
                self._update_identity("known", kname, min(r[2] for r in known_rows if r[2] is not None)
                                      if any(r[2] is not None for r in known_rows) else None,
                                      min(klive) if klive else None)
            else:
                self._update_identity(status, name, score, liveness)
        else:
            self._update_face_alerts(status, name)

    @staticmethod
    def _face_label(verdict: str, name: str | None, score: float | None, live: float | None, small: bool) -> str:
        """Étiquette incrustée (police Hershey : ASCII seulement, accents retirés)."""
        sim = f" {score:.2f}" if score is not None else ""
        viv = f" | vivant {live:.2f}" if live is not None else ""
        if verdict == "spoof":
            text = f"LEURRE {live:.2f}" if live is not None else "LEURRE"
        elif verdict == "known":
            text = f"{name}{sim}{viv}"
        elif verdict == "checking":
            text = f"{name}? verification" + (" (approchez)" if small else (f" {live:.2f}" if live is not None else ""))
        else:
            text = f"inconnu{sim}{viv}"
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

    def _note_face_absent(self) -> None:
        """Caméra coupée : plus de visage, pour laisser retomber l'alarme inconnue."""
        if not self._face_engine.ready:
            return
        with self._lock:
            self._face_live = {"status": "none", "name": None, "score": None, "faces": 0,
                               "liveness": None, "live_state": None}
            self._face_marks = []
        self._tracker.reset()
        if self.identify_on:
            with self._lock:
                self._persons = 0
            self._update_identity("none", None, None, None)
        else:
            self._update_face_alerts("none", None)

    def _update_identity(self, status: str, name: str | None, score: float | None, liveness: float | None) -> None:
        """Portillon : bip « identifiez-vous », fenêtre, autorisé ou intrusion (+ Discord)."""
        now_m = time.monotonic()
        events = self._identity.update(now_m, self._persons > 0, status, name, score, liveness)
        # capture différée (~1 s après la détection) : la personne est alors bien dans le cadre
        due = getattr(self, "_capture_due", None)
        if due is not None and now_m >= due and self._identity.state == IDENTIFYING:
            try:
                self._capture_jpeg = self.get_jpeg()
            except Exception:  # noqa: BLE001
                self._capture_jpeg = None
            self._capture_due = None
        for ev in events:
            if ev.kind == "idle":
                logger.info("Identification : plus personne, retour au repos")
                self._capture_jpeg = None
                self._capture_due = None
                self._post_alert("identify_end", 0, None)
                continue
            if ev.kind == "identify_start":
                logger.info("Identification : personne détectée, « identifiez-vous » (%.0f s)", IDENT_CFG.window_s)
                # capture programmée CAPTURE_DELAY_S après la détection ; envoyée seulement si la
                # personne ne s'identifie pas
                self._capture_jpeg = None
                self._capture_due = now_m + CAPTURE_DELAY_S
            elif ev.kind == "identified":
                logger.info("Identification : autorisé %s", ev.name)
                self._capture_jpeg = None  # identifié : on oublie la capture
                self._capture_due = None
            else:
                logger.warning("Identification : INTRUSION (%s) — non identifié après %.0f s",
                               "leurre" if ev.kind == "intrusion_spoof" else "inconnu", IDENT_CFG.window_s)
                info = dict(ev.info)
                info["window_s"] = IDENT_CFG.window_s
                jpeg, self._capture_jpeg, self._capture_due = getattr(self, "_capture_jpeg", None), None, None
                self._discord.alert("spoof" if ev.kind == "intrusion_spoof" else "unknown", info, jpeg=jpeg)
            self._post_alert(ev.kind, self._persons, None)

    def _update_face_alerts(self, status: str, name: str | None) -> None:
        now = time.monotonic()
        if status == "checking":
            # preuve de vivacité insuffisante : ni « connu » ni alarme ; un « connu » devra
            # ensuite rester stable FACE_DEBOUNCE avant d'être publié.
            self._face_pending = status
            self._face_pending_name = None
            self._face_since = now
            return
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
        elif status == "spoof":
            logger.warning("Leurre détecté (photo/écran) : tentative d'usurpation")
            self._post_alert("face_spoof", self._persons, None)
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
        face_marks: list[tuple[int, int, int, int, str, str]] | None = None,
        scale: float = 1.0,
    ) -> np.ndarray:
        """Incruste les détections (coordonnées de la trame d'inférence × scale) sur la trame diffusée."""
        out = frame
        k = max(scale, 0.5)
        fs, ft = 0.5 * k, max(1, int(round(k)))

        def p(v: float) -> int:
            return int(round(v * scale))

        for x1, y1, x2, y2, conf, label in boxes:
            x1, y1, x2, y2 = p(x1), p(y1), p(x2), p(y2)
            cv2.rectangle(out, (x1, y1), (x2, y2), (59, 224, 192), max(2, ft + 1))
            tag = f"{label} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, ft)
            cv2.rectangle(out, (x1, max(0, y1 - th - p(6))), (x1 + tw + p(4), y1), (59, 224, 192), -1)
            cv2.putText(out, tag, (x1 + p(2), y1 - p(4)), cv2.FONT_HERSHEY_SIMPLEX, fs, (10, 20, 30), ft, cv2.LINE_AA)
        for x, y, bw, bh, label, kind in face_marks or []:
            x, y, bw, bh = p(x), p(y), p(bw), p(bh)
            # BGR sobres : connu vert, inconnu rouge brique, leurre orange (accent), vérif. gris
            color = {"known": (74, 125, 47), "spoof": (38, 92, 196), "checking": (110, 108, 102)}.get(
                kind, (46, 58, 163))
            cv2.rectangle(out, (x, y), (x + bw, y + bh), color, (3 if kind == "spoof" else 2) * ft)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, ft)
            y0 = y + bh + th + p(6)
            if y0 > out.shape[0]:
                y0 = max(th + p(4), y - p(4))
            cv2.rectangle(out, (x, y0 - th - p(4)), (x + tw + p(4), y0 + p(2)), color, -1)
            cv2.putText(out, label, (x + p(2), y0), cv2.FONT_HERSHEY_SIMPLEX, fs, (236, 241, 242), ft, cv2.LINE_AA)
        ident = self._identity.snapshot(time.monotonic()) if self.identify_on else None
        if ident and ident["state"] != "idle":
            if ident["state"] == IDENTIFYING:
                text, bg = f"IDENTIFIEZ-VOUS  {int(round(ident['remaining_s'] or 0))} s", (70, 68, 64)
            elif ident["state"] == AUTHORIZED:
                text, bg = f"AUTORISE : {ident['name'] or '?'}", (74, 125, 47)
            elif ident["state"] == INTRUSION and ident["kind"] == "spoof":
                text, bg = "INTRUSION - LEURRE (photo/ecran)", (38, 92, 196)
            else:
                text, bg = "INTRUSION - non identifie", (46, 58, 163)
            text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * k, ft)
            x0 = (out.shape[1] - tw) // 2 - p(10)
            y0 = p(32)
            cv2.rectangle(out, (x0, y0), (x0 + tw + p(20), y0 + th + p(14)), bg, -1)
            cv2.putText(out, text, (x0 + p(10), y0 + th + p(7)), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * k,
                        (236, 241, 242), ft, cv2.LINE_AA)
        banner = f"SENTINEL-X IA  |  {fps:.1f} FPS  |  inference {self._infer_ms:.0f} ms  |  personnes: {persons}"
        cv2.rectangle(out, (0, 0), (out.shape[1], p(28)), (12, 18, 32), -1)
        cv2.putText(out, banner, (p(8), p(20)), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * k, (59, 224, 192), ft, cv2.LINE_AA)
        return out

    def _capture_loop(self) -> None:
        """Lit la webcam à sa cadence native et publie la dernière trame (sans traitement)."""
        cap: cv2.VideoCapture | None = None
        frame_times: list[float] = []
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
                        self._stop.wait(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 1.5, 10.0)
                        continue
                    reconnect_delay = 1.0
                    frame_times = []
                    logger.info("Camera opened")

                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Camera read failed — reconnecting")
                    with self._lock:
                        self._camera_ok = False
                    cap.release()
                    cap = None
                    self._stop.wait(0.5)
                    continue

                now = time.monotonic()
                with self._frame_cond:
                    self._frame = frame
                    self._frame_seq += 1
                    self._frame_cond.notify_all()
                frame_times.append(now)
                frame_times = [t for t in frame_times if t >= now - 2.0]
                with self._lock:
                    self._camera_ok = True
                    self._capture_shape = (frame.shape[1], frame.shape[0])
                    if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]:
                        self._fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
            except Exception as exc:  # noqa: BLE001
                logger.exception("Capture loop error: %s", exc)
                with self._lock:
                    self._camera_ok = False
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                self._stop.wait(1.0)
        if cap is not None:
            cap.release()

    def _infer_loop(self) -> None:
        """YOLO + reconnaissance sur la trame la plus récente, réduite à INFER_HEIGHT."""
        assert self._model is not None
        # le nombre de threads OpenMP est un réglage par fil : on le refixe dans ce fil
        torch.set_num_threads(max(1, _nt))
        cv2.setNumThreads(1)
        last_seq = -1
        last_infer = 0.0
        last_face = 0.0
        last_absent = 0.0
        infer_times: list[float] = []
        while not self._stop.is_set():
            try:
                seq = self.wait_frame(last_seq, 1.0)
                now = time.monotonic()
                with self._lock:
                    camera_ok = self._camera_ok
                if not camera_ok:
                    # caméra coupée : plus de visage, pour laisser retomber les alarmes
                    if now - last_absent >= 1.0:
                        self._note_face_absent()
                        last_absent = now
                    continue
                if seq == last_seq:
                    continue
                wait = INFER_INTERVAL - (now - last_infer)
                if wait > 0:
                    self._stop.wait(wait)  # puis on reprend la trame la plus récente
                    continue
                with self._frame_cond:
                    frame, seq = self._frame, self._frame_seq
                if frame is None:
                    continue
                last_seq = seq
                small = _resize_h(frame, INFER_HEIGHT)
                if small is frame:
                    small = frame.copy()
                lifted = lift_shadows(small)
                with self._lock:
                    self._raw_frame = lifted  # trame sans incrustation, pour l'enrôlement par capture

                t0 = time.monotonic()
                results = self._model.predict(
                    lifted, verbose=False, device="cpu", classes=[PERSON_CLASS_ID], conf=0.45
                )
                boxes: list[tuple[int, int, int, int, float, str]] = []
                max_conf = 0.0
                for r in results:
                    if r.boxes is None:
                        continue
                    for b in r.boxes:
                        conf = float(b.conf[0])
                        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                        boxes.append((x1, y1, x2, y2, conf, "person"))
                        max_conf = max(max_conf, conf)
                persons = len(boxes)
                last_infer = now
                dt = time.monotonic() - t0
                infer_times = (infer_times + [dt])[-30:]
                avg = sum(infer_times) / len(infer_times)
                with self._lock:
                    self._last_boxes = boxes
                    self._infer_fps = 1.0 / avg if avg > 0 else 0.0
                    self._infer_ms = avg * 1000.0
                    self._infer_ms_last = dt * 1000.0
                    self._infer_ms_max = max(infer_times) * 1000.0
                    self._infer_shape = (lifted.shape[1], lifted.shape[0])
                    self._persons = persons
                    self._infer_runs = [t for t in self._infer_runs if t >= now - 5.0] + [now]
                self._update_person_alerts(persons, max_conf)
                if now - last_face >= FACE_INTERVAL:
                    self._recognize(lifted, small)
                    last_face = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Inference loop error: %s", exc)
                self._stop.wait(1.0)

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
        """Flux MJPEG continu : une trame par nouvelle capture, plafonné à ?fps= (≤ STREAM_FPS)."""
        try:
            want = float(parse_qs(urlsplit(self.path).query).get("fps", [STREAM_FPS])[0])
        except (TypeError, ValueError):
            want = STREAM_FPS
        interval = 1.0 / min(max(want, 1.0), STREAM_FPS)
        if not WORKER.stream_slot(+1):
            self._send_json(503, {"error": "trop de flux ouverts"})
            return
        try:
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            self.close_connection = True
            seq = -1
            next_at = 0.0
            while not WORKER._stop.is_set():  # noqa: SLF001
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                seq = WORKER.wait_frame(seq, 1.0)  # si la caméra est coupée : renvoi ~1/s (placeholder)
                next_at = time.monotonic() + interval
                jpeg = WORKER.get_jpeg()
                if not jpeg:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                 + f"Content-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            WORKER.stream_slot(-1)


def main() -> None:
    WORKER.start()
    # Wait briefly for first frame
    for _ in range(50):
        if WORKER.snapshot()["camera_ok"]:
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
