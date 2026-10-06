"""Anti-spoofing passif (photo imprimée, écran) : MiniFASNet + lissage temporel par piste.

Modèles : Silent-Face-Anti-Spoofing (Minivision, Apache-2.0), MiniFASNetV2 échelle 2.7 et
MiniFASNetV1SE échelle 4.0, entrées 80x80 BGR en 0..255 (pas de /255 : to_tensor modifié
en amont), softmax 3 classes [0 = photo, 1 = vrai visage, 2 = écran], moyenne des deux.
Conversions ONNX vérifiées identiques aux poids .pth officiels (écart < 1e-6).

Le découpage reprend exactement CropImage._get_new_box du dépôt d'origine. Le dépôt utilise
un détecteur RetinaFace dont la boîte est plus carrée que celle de YuNet : on convertit la
boîte YuNet en boîte « type RetinaFace » (calibré sur nos trames : largeur x1,13,
hauteur x0,92, centre abaissé de 5 % de la hauteur). Sans cette conversion les vrais
visages tombent de ~0,99 à ~0,6-0,7 sur une partie des trames.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LIVE_MODELS = (
    ("2.7_80x80_MiniFASNetV2.onnx", 2.7),
    ("4_0_0_80x80_MiniFASNetV1SE.onnx", 4.0),
)
INPUT_SIZE = 80
REAL_CLASS = 1

# YuNet (x, y, w, h) -> boîte équivalente RetinaFace (détecteur d'entraînement)
RETINA_W = 1.13
RETINA_H = 0.92
RETINA_DY = 0.05


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class LivenessConfig:
    enabled: bool = True
    live_threshold: float = 0.60   # moyenne lissée >= seuil -> vivant
    spoof_threshold: float = 0.30  # moyenne lissée <= seuil -> leurre
    alpha: float = 0.40            # poids de la nouvelle mesure (EMA)
    min_obs: int = 2               # mesures minimales avant « vivant »
    spoof_min_obs: int = 4         # « leurre » exige plus de preuves (~1,2 s) : pas de fausse alerte
    min_face: int = 48             # largeur YuNet mini (px) pour mesurer ; sinon « vérification »
    every: int = 1                 # mesure toutes les N passes visage par piste
    enroll_threshold: float = 0.60  # une seule image à l'enrôlement
    track_ttl: float = 1.5         # piste oubliée après N s sans visage

    @classmethod
    def from_env(cls) -> "LivenessConfig":
        return cls(
            enabled=os.environ.get("SENTINEL_LIVENESS", "1").strip().lower() not in ("0", "false", "no", "off"),
            live_threshold=_env_float("SENTINEL_LIVENESS_LIVE", cls.live_threshold),
            spoof_threshold=_env_float("SENTINEL_LIVENESS_SPOOF", cls.spoof_threshold),
            alpha=min(1.0, max(0.05, _env_float("SENTINEL_LIVENESS_ALPHA", cls.alpha))),
            min_obs=max(1, int(_env_float("SENTINEL_LIVENESS_MIN_OBS", cls.min_obs))),
            spoof_min_obs=max(1, int(_env_float("SENTINEL_LIVENESS_SPOOF_MIN_OBS", cls.spoof_min_obs))),
            min_face=max(16, int(_env_float("SENTINEL_LIVENESS_MIN_FACE", cls.min_face))),
            every=max(1, int(_env_float("SENTINEL_LIVENESS_EVERY", cls.every))),
            enroll_threshold=_env_float("SENTINEL_LIVENESS_ENROLL", cls.enroll_threshold),
            track_ttl=_env_float("SENTINEL_LIVENESS_TTL", cls.track_ttl),
        )


def retina_like_box(box: tuple[int, int, int, int] | list[int]) -> tuple[int, int, int, int]:
    """Boîte YuNet (x, y, w, h) -> boîte façon RetinaFace (x, y, w, h)."""
    x, y, w, h = (float(v) for v in box[:4])
    cx = x + w / 2.0
    cy = y + h / 2.0 + RETINA_DY * h
    nw, nh = max(1.0, RETINA_W * w), max(1.0, RETINA_H * h)
    return int(cx - nw / 2.0), int(cy - nh / 2.0), int(round(nw)), int(round(nh))


def crop_box(src_w: int, src_h: int, bbox, scale: float) -> tuple[int, int, int, int]:
    """Copie fidèle de CropImage._get_new_box (Silent-Face-Anti-Spoofing).

    Retourne (x0, y0, x1, y1) inclusifs ; la découpe est img[y0:y1+1, x0:x1+1].
    """
    x, y, box_w, box_h = bbox[0], bbox[1], max(bbox[2], 1), max(bbox[3], 1)
    scale = min((src_h - 1) / box_h, min((src_w - 1) / box_w, scale))
    new_width = box_w * scale
    new_height = box_h * scale
    center_x, center_y = box_w / 2 + x, box_h / 2 + y
    left_top_x = center_x - new_width / 2
    left_top_y = center_y - new_height / 2
    right_bottom_x = center_x + new_width / 2
    right_bottom_y = center_y + new_height / 2
    if left_top_x < 0:
        right_bottom_x -= left_top_x
        left_top_x = 0
    if left_top_y < 0:
        right_bottom_y -= left_top_y
        left_top_y = 0
    if right_bottom_x > src_w - 1:
        left_top_x -= right_bottom_x - src_w + 1
        right_bottom_x = src_w - 1
    if right_bottom_y > src_h - 1:
        left_top_y -= right_bottom_y - src_h + 1
        right_bottom_y = src_h - 1
    return int(left_top_x), int(left_top_y), int(right_bottom_x), int(right_bottom_y)


def _softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    e = np.exp(x - x.max())
    return e / e.sum()


class LivenessModel:
    """Fusion des deux MiniFASNet (OpenCV DNN, CPU, ~1,3 ms par modèle et par visage)."""

    def __init__(self, model_dir: Path) -> None:
        self.ready = False
        self.detail = "anti-spoofing absent — lancer fetch_face_models.py"
        self._nets: list[tuple[object, float]] = []
        self._lock = threading.Lock()
        paths = [(Path(model_dir) / name, scale) for name, scale in LIVE_MODELS]
        if not all(p.is_file() for p, _ in paths):
            return
        try:
            import cv2

            self._cv2 = cv2
            self._nets = [(cv2.dnn.readNetFromONNX(str(p)), scale) for p, scale in paths]
        except Exception as exc:  # noqa: BLE001
            self.detail = f"anti-spoofing: {exc}"
            self._nets = []
            return
        self.ready = True
        self.detail = "minifasnet v2+v1se"

    def crops(self, image: np.ndarray, box) -> list[np.ndarray]:
        """Patchs 80x80 (BGR uint8) tels que vus par chaque modèle."""
        cv2 = self._cv2
        height, width = image.shape[:2]
        rbox = retina_like_box(box)
        out = []
        for _net, scale in self._nets:
            x0, y0, x1, y1 = crop_box(width, height, rbox, scale)
            patch = image[max(0, y0): y1 + 1, max(0, x0): x1 + 1]
            if patch.size == 0:
                return []
            out.append(cv2.resize(patch, (INPUT_SIZE, INPUT_SIZE)))
        return out

    def probs(self, image: np.ndarray, box) -> np.ndarray | None:
        """Probabilités fusionnées [photo, vrai, écran] ou None."""
        if not self.ready or image is None:
            return None
        patches = self.crops(image, box)
        if len(patches) != len(self._nets):
            return None
        total = np.zeros(3, dtype=np.float64)
        with self._lock:
            for (net, _scale), patch in zip(self._nets, patches):
                blob = patch.transpose(2, 0, 1)[None].astype(np.float32)  # 0..255, BGR
                net.setInput(blob)
                total += _softmax(net.forward())
        return total / len(self._nets)

    def score(self, image: np.ndarray, box) -> float | None:
        p = self.probs(image, box)
        return None if p is None else float(p[REAL_CLASS])


# ------------------------------------------------------------------ temporel


def decide(previous: str, ema: float | None, n_obs: int, cfg: LivenessConfig) -> str:
    """live | spoof | checking, avec hystérésis entre les deux seuils."""
    if ema is None or n_obs < cfg.min_obs:
        return "checking"
    if ema <= cfg.spoof_threshold:
        return "spoof" if n_obs >= cfg.spoof_min_obs else "checking"
    if ema >= cfg.live_threshold:
        return "live"
    return previous if previous in ("live", "spoof") else "checking"


@dataclass
class Track:
    track_id: int
    box: tuple[int, int, int, int]
    last_seen: float
    ema: float | None = None
    n_obs: int = 0
    passes: int = 0
    state: str = "checking"
    last_score: float | None = None
    small: bool = False
    history: list[float] = field(default_factory=list)

    def observe(self, score: float, cfg: LivenessConfig) -> None:
        score = float(min(1.0, max(0.0, score)))
        self.last_score = score
        self.ema = score if self.ema is None else cfg.alpha * score + (1.0 - cfg.alpha) * self.ema
        self.n_obs += 1
        self.history = (self.history + [score])[-10:]
        self.state = decide(self.state, self.ema, self.n_obs, cfg)


def _iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class LivenessTracker:
    """Associe les visages d'une passe à l'autre (IoU / centre) et lisse le score par piste."""

    def __init__(self, cfg: LivenessConfig) -> None:
        self.cfg = cfg
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def assign(self, boxes: list[tuple[int, int, int, int]], now: float) -> list[Track]:
        for tid in [t for t, tr in self.tracks.items() if now - tr.last_seen > self.cfg.track_ttl]:
            self.tracks.pop(tid)
        free = dict(self.tracks)
        out: list[Track] = []
        for box in boxes:
            best, best_val = None, 0.0
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            for tid, tr in free.items():
                val = _iou(box, tr.box)
                tx, ty = tr.box[0] + tr.box[2] / 2, tr.box[1] + tr.box[3] / 2
                if val < 0.2 and abs(cx - tx) < 0.5 * box[2] and abs(cy - ty) < 0.5 * box[3]:
                    val = 0.2
                if val > best_val:
                    best, best_val = tid, val
            if best is not None and best_val >= 0.2:
                tr = free.pop(best)
                tr.box = tuple(int(v) for v in box[:4])
            else:
                tr = Track(self._next, tuple(int(v) for v in box[:4]), now)
                self.tracks[tr.track_id] = tr
                self._next += 1
            tr.last_seen = now
            tr.passes += 1
            tr.small = box[2] < self.cfg.min_face
            out.append(tr)
        return out

    def wants_measure(self, tr: Track) -> bool:
        if tr.small:
            return False
        every = self.cfg.every
        if tr.state == "live" and tr.n_obs >= 6 and (tr.ema or 0) >= 0.9:
            every = max(every, 3)  # piste déjà sûre : on économise le CPU (plusieurs visages)
        return tr.n_obs == 0 or (tr.passes - 1) % every == 0

    def reset(self) -> None:
        self.tracks.clear()


def face_verdict(known: bool, live_state: str, liveness_on: bool) -> str:
    """Statut d'un visage : known | unknown | spoof | checking."""
    if not liveness_on:
        return "known" if known else "unknown"
    if live_state == "spoof":
        return "spoof"
    if not known:
        return "unknown"
    return "known" if live_state == "live" else "checking"


_PRIORITY = {"spoof": 4, "unknown": 3, "checking": 2, "known": 1}


def aggregate_status(verdicts: list[str]) -> str:
    """Plusieurs visages : leurre > inconnu > vérification > connu ; aucun -> none."""
    if not verdicts:
        return "none"
    return max(verdicts, key=lambda v: _PRIORITY.get(v, 0))
