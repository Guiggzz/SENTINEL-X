"""Anti-spoofing : géométrie de découpe (fidèle au dépôt d'origine), lissage, décisions, enrôlement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from face_api import SPOOF_ERROR, enroll_images
from face_gallery import Gallery
from face_liveness import (
    LivenessConfig,
    LivenessModel,
    LivenessTracker,
    Track,
    aggregate_status,
    crop_box,
    decide,
    face_verdict,
    retina_like_box,
)

CFG = LivenessConfig(live_threshold=0.70, spoof_threshold=0.35, alpha=0.4, min_obs=3, spoof_min_obs=3, min_face=56)
MODELS = Path(__file__).resolve().parent / "models"


class CropGeometryTests(unittest.TestCase):
    def test_centered_box_scale_27(self) -> None:
        self.assertEqual(crop_box(640, 480, (100, 100, 50, 50), 2.7), (57, 57, 192, 192))

    def test_left_top_edge_shifts_window(self) -> None:
        self.assertEqual(crop_box(640, 480, (0, 10, 40, 40), 4.0), (0, 0, 160, 160))

    def test_right_edge_shifts_window(self) -> None:
        self.assertEqual(crop_box(640, 480, (600, 400, 40, 40), 2.7), (531, 366, 639, 474))

    def test_scale_clamped_to_image(self) -> None:
        self.assertEqual(crop_box(400, 400, (10, 10, 300, 300), 4.0), (0, 0, 399, 399))

    def test_window_always_inside_image(self) -> None:
        rng = np.random.default_rng(0)
        for _ in range(200):
            w, h = int(rng.integers(100, 1300)), int(rng.integers(100, 800))
            bw, bh = int(rng.integers(10, min(w, h) // 2)), int(rng.integers(10, min(w, h) // 2))
            box = (int(rng.integers(0, w - bw)), int(rng.integers(0, h - bh)), bw, bh)
            for scale in (2.7, 4.0):
                x0, y0, x1, y1 = crop_box(w, h, box, scale)
                self.assertGreaterEqual(x0, 0)
                self.assertGreaterEqual(y0, 0)
                self.assertLessEqual(x1, w - 1)
                self.assertLessEqual(y1, h - 1)

    def test_yunet_to_retina_box(self) -> None:
        # plus large (x1,13), moins haute (x0,92), centre abaissé de 5 % de la hauteur
        self.assertEqual(retina_like_box((100, 100, 100, 120)), (93, 110, 113, 110))


class DecisionTests(unittest.TestCase):
    def test_needs_min_observations(self) -> None:
        self.assertEqual(decide("checking", 0.99, 2, CFG), "checking")
        self.assertEqual(decide("checking", 0.99, 3, CFG), "live")
        self.assertEqual(decide("checking", 0.10, 3, CFG), "spoof")

    def test_spoof_needs_more_evidence_than_live(self) -> None:
        cfg = LivenessConfig(min_obs=2, spoof_min_obs=4)
        self.assertEqual(decide("checking", 0.05, 2, cfg), "checking")
        self.assertEqual(decide("checking", 0.05, 4, cfg), "spoof")
        self.assertEqual(decide("checking", 0.95, 2, cfg), "live")

    def test_hysteresis_between_thresholds(self) -> None:
        self.assertEqual(decide("checking", 0.5, 5, CFG), "checking")
        self.assertEqual(decide("live", 0.5, 5, CFG), "live")
        self.assertEqual(decide("spoof", 0.5, 5, CFG), "spoof")

    def test_single_bad_frame_does_not_flip_live(self) -> None:
        tr = Track(1, (0, 0, 100, 100), 0.0)
        for s in (0.98, 0.97, 0.99, 0.96):
            tr.observe(s, CFG)
        self.assertEqual(tr.state, "live")
        tr.observe(0.05, CFG)  # une trame ratée
        self.assertEqual(tr.state, "live")

    def test_sustained_spoof_flips_in_few_observations(self) -> None:
        tr = Track(1, (0, 0, 100, 100), 0.0)
        for s in (0.98, 0.97, 0.99):
            tr.observe(s, CFG)
        flips = 0
        for s in (0.05, 0.08, 0.04, 0.06):
            tr.observe(s, CFG)
            flips += 1
            if tr.state == "spoof":
                break
        self.assertEqual(tr.state, "spoof")
        self.assertLessEqual(flips, 4)

    def test_photo_never_reaches_live(self) -> None:
        tr = Track(1, (0, 0, 100, 100), 0.0)
        for s in (0.2, 0.6, 0.1, 0.3, 0.15, 0.4):
            tr.observe(s, CFG)
            self.assertNotEqual(tr.state, "live")

    def test_verdicts(self) -> None:
        self.assertEqual(face_verdict(True, "live", True), "known")
        self.assertEqual(face_verdict(True, "checking", True), "checking")
        self.assertEqual(face_verdict(True, "spoof", True), "spoof")
        self.assertEqual(face_verdict(False, "spoof", True), "spoof")
        self.assertEqual(face_verdict(False, "checking", True), "unknown")
        self.assertEqual(face_verdict(True, "checking", False), "known")  # anti-spoofing absent

    def test_aggregate_priority(self) -> None:
        self.assertEqual(aggregate_status([]), "none")
        self.assertEqual(aggregate_status(["known", "spoof"]), "spoof")
        self.assertEqual(aggregate_status(["known", "unknown", "checking"]), "unknown")
        self.assertEqual(aggregate_status(["known", "checking"]), "checking")
        self.assertEqual(aggregate_status(["known", "known"]), "known")

    def test_config_from_env(self) -> None:
        import os

        old = dict(os.environ)
        try:
            os.environ.update({"SENTINEL_LIVENESS_LIVE": "0.8", "SENTINEL_LIVENESS_MIN_OBS": "5",
                               "SENTINEL_LIVENESS": "0"})
            cfg = LivenessConfig.from_env()
            self.assertEqual((cfg.live_threshold, cfg.min_obs, cfg.enabled), (0.8, 5, False))
        finally:
            os.environ.clear()
            os.environ.update(old)


class TrackerTests(unittest.TestCase):
    def test_same_face_keeps_track_and_new_face_gets_new(self) -> None:
        tk = LivenessTracker(CFG)
        a = tk.assign([(100, 100, 80, 80)], 0.0)[0]
        b = tk.assign([(106, 104, 82, 80), (400, 100, 80, 80)], 0.3)
        self.assertIs(b[0], a)
        self.assertIsNot(b[1], a)

    def test_track_expires(self) -> None:
        tk = LivenessTracker(CFG)
        a = tk.assign([(100, 100, 80, 80)], 0.0)[0]
        b = tk.assign([(100, 100, 80, 80)], 5.0)[0]
        self.assertIsNot(a, b)

    def test_small_face_not_measured(self) -> None:
        tk = LivenessTracker(CFG)
        tr = tk.assign([(100, 100, 30, 30)], 0.0)[0]
        self.assertTrue(tr.small)
        self.assertFalse(tk.wants_measure(tr))

    def test_measure_every_n(self) -> None:
        tk = LivenessTracker(LivenessConfig(every=3, min_face=10))
        got = []
        for i in range(7):
            tr = tk.assign([(100, 100, 80, 80)], i * 0.3)[0]
            got.append(tk.wants_measure(tr))
            if got[-1]:
                tr.observe(0.9, tk.cfg)
        self.assertEqual(got, [True, False, False, True, False, False, True])


class _Det:
    def __init__(self, live_checked: bool, live_score: float | None) -> None:
        v = np.ones(8, dtype=np.float32)
        self.embedding = v / np.linalg.norm(v)
        self.thumb_jpeg = b"thumb"
        self.live_checked = live_checked
        self.live_score = live_score


class _Embedder:
    ready = True
    enroll_live_threshold = 0.6

    def __init__(self, scores) -> None:
        self.scores = list(scores)

    def embed_bytes(self, data: bytes):
        s = self.scores.pop(0)
        return _Det(s != "off", None if s in ("off", "small") else s)


class EnrollLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gallery = Gallery(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_spoofed_capture_rejected_422(self) -> None:
        code, body = enroll_images(self.gallery, _Embedder([0.12]), "Eve", [b"x"], single=True)
        self.assertEqual(code, 422)
        self.assertEqual(body["error"], SPOOF_ERROR)
        self.assertEqual(self.gallery.count(), 0)

    def test_one_spoof_in_batch_rejects_all(self) -> None:
        code, _ = enroll_images(self.gallery, _Embedder([0.98, 0.2]), "Eve", [b"a", b"b"])
        self.assertEqual(code, 422)
        self.assertEqual(self.gallery.count(), 0)

    def test_live_capture_enrolled(self) -> None:
        code, body = enroll_images(self.gallery, _Embedder([0.97]), "Alice", [b"x"], single=True)
        self.assertEqual(code, 201)
        self.assertEqual(body["samples"], 1)

    def test_too_small_face_rejected(self) -> None:
        code, body = enroll_images(self.gallery, _Embedder(["small"]), "Alice", [b"x"])
        self.assertEqual(code, 422)
        self.assertIn("petit", body["error"])

    def test_no_liveness_model_keeps_old_behaviour(self) -> None:
        code, _ = enroll_images(self.gallery, _Embedder(["off"]), "Alice", [b"x"])
        self.assertEqual(code, 201)


@unittest.skipUnless((MODELS / "2.7_80x80_MiniFASNetV2.onnx").is_file(), "modèles anti-spoofing absents")
class ModelSmokeTests(unittest.TestCase):
    def test_probabilities_and_patch_size(self) -> None:
        model = LivenessModel(MODELS)
        self.assertTrue(model.ready)
        img = np.random.default_rng(1).integers(0, 255, (440, 782, 3), dtype=np.uint8)
        patches = model.crops(img, (350, 100, 100, 120))
        self.assertEqual([p.shape for p in patches], [(80, 80, 3), (80, 80, 3)])
        probs = model.probs(img, (350, 100, 100, 120))
        self.assertEqual(probs.shape, (3,))
        self.assertAlmostEqual(float(probs.sum()), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
