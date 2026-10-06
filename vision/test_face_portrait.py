"""Photo badge : cadre 3:4, de face, première capture, vignette chronologique."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from face_engine import frontal_score, portrait_box
from face_gallery import Gallery


class PortraitBoxTests(unittest.TestCase):
    def test_ratio_and_face_position(self) -> None:
        x0, y0, x1, y1 = portrait_box((600, 300, 100, 120), 1920, 1080)
        w, h = x1 - x0, y1 - y0
        self.assertAlmostEqual(h / w, 4 / 3, delta=0.02)
        self.assertGreaterEqual(w, 220)
        self.assertAlmostEqual(((300 + 60) - y0) / h, 0.45, delta=0.02)
        self.assertAlmostEqual(((600 + 50) - x0) / w, 0.5, delta=0.02)

    def test_clamped_inside_image(self) -> None:
        x0, y0, x1, y1 = portrait_box((5, 5, 200, 220), 640, 480)
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x1, 640)
        self.assertLessEqual(y1, 480)
        self.assertAlmostEqual((y1 - y0) / (x1 - x0), 4 / 3, delta=0.02)

    def test_frontal_score(self) -> None:
        self.assertLess(frontal_score((100, 50, 160, 50, 130, 80)), 0.05)
        self.assertGreater(frontal_score((100, 50, 130, 50, 140, 80)), 0.5)
        self.assertEqual(frontal_score(None), 1.0)


class GalleryPortraitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gallery = Gallery(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_portrait_kept_and_thumb_is_first(self) -> None:
        emb = [np.ones(4, dtype=np.float32) for _ in range(11)]
        p = self.gallery.enroll("Alice", emb[:1], [b"t0"], portrait=b"first")
        self.gallery.enroll("Alice", emb[1:], [f"t{i}".encode() for i in range(1, 11)], portrait=b"later")
        self.assertEqual(self.gallery.portrait_file(p["id"]).read_bytes(), b"first")
        self.assertEqual(self.gallery.thumb_file(p["id"]).read_bytes(), b"t0")
        self.assertTrue(self.gallery.list_people()[0]["portrait"])

    def test_set_portrait_keeps_embeddings(self) -> None:
        p = self.gallery.enroll("Bob", [np.ones(4, dtype=np.float32)], [b"t"])
        self.assertFalse(self.gallery.list_people()[0]["portrait"])
        self.assertIsNone(self.gallery.portrait_file(p["id"]))
        self.assertTrue(self.gallery.set_portrait(p["id"], b"new"))
        self.assertEqual(self.gallery.portrait_file(p["id"]).read_bytes(), b"new")
        self.assertEqual(self.gallery.list_people()[0]["samples"], 1)
        self.assertFalse(self.gallery.set_portrait("../x", b"new"))
        self.assertIsNone(self.gallery.portrait_file("../etc"))


if __name__ == "__main__":
    unittest.main()
