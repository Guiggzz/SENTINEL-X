"""Galerie visages : enrôlement, similarité cosinus, suppression."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

import numpy as np

from face_gallery import Gallery, cosine, identify


def _emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(128).astype(np.float32)
    return v / np.linalg.norm(v)


class CosineTests(unittest.TestCase):
    def test_identical_is_one(self) -> None:
        v = _emb(1)
        self.assertAlmostEqual(cosine(v, v), 1.0, places=5)

    def test_orthogonal_is_near_zero(self) -> None:
        a = np.zeros(4, dtype=np.float32)
        b = np.zeros(4, dtype=np.float32)
        a[0] = 1
        b[1] = 1
        self.assertAlmostEqual(cosine(a, b), 0.0, places=5)


class IdentifyTests(unittest.TestCase):
    def test_known_above_threshold(self) -> None:
        alice = _emb(3)
        gallery = [("a", "Alice", alice)]
        hit = identify(alice, gallery, 0.363)
        self.assertEqual(hit.status, "known")
        self.assertEqual(hit.name, "Alice")
        self.assertEqual(hit.person_id, "a")
        self.assertGreaterEqual(hit.score, 0.363)

    def test_unknown_below_threshold(self) -> None:
        gallery = [("a", "Alice", _emb(3))]
        hit = identify(_emb(9), gallery, 0.99)
        self.assertEqual(hit.status, "unknown")
        self.assertIsNone(hit.name)

    def test_empty_gallery_is_unknown(self) -> None:
        hit = identify(_emb(1), [], 0.363)
        self.assertEqual(hit.status, "unknown")
        self.assertIsNone(hit.score)

    def test_best_of_several_samples(self) -> None:
        weak = _emb(4)
        strong = _emb(5)
        query = strong * 0.2 + weak * 0.02
        query = query / np.linalg.norm(query)
        gallery = [("p", "Bob", weak), ("p", "Bob", strong)]
        hit = identify(query, gallery, 0.3)
        self.assertEqual(hit.name, "Bob")
        self.assertGreater(hit.score, cosine(query, weak))


class GalleryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gallery = Gallery(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_enroll_list_match_delete(self) -> None:
        thumb = b"\xff\xd8\xff\xd9"
        person = self.gallery.enroll("Alice Martin", [_emb(2)], [thumb])
        self.assertEqual(person["name"], "Alice Martin")
        self.assertEqual(person["samples"], 1)
        listed = self.gallery.list_people()
        self.assertEqual(len(listed), 1)
        self.assertNotIn("embedding", listed[0])
        hit = self.gallery.match(_emb(2), 0.363)
        self.assertEqual(hit.status, "known")
        self.assertEqual(hit.name, "Alice Martin")
        thumb_path = self.gallery.thumb_file(person["id"])
        self.assertIsNotNone(thumb_path)
        assert thumb_path is not None
        self.assertTrue(thumb_path.is_file())
        self.assertTrue(self.gallery.delete(person["id"]))
        self.assertEqual(self.gallery.list_people(), [])
        self.assertEqual(self.gallery.match(_emb(2), 0.363).status, "unknown")

    def test_second_photo_adds_sample(self) -> None:
        first = self.gallery.enroll("Camille", [_emb(6)], [b"a"])
        again = self.gallery.enroll("Camille", [_emb(7)], [b"b"], person_id=first["id"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["samples"], 2)
        self.assertEqual(self.gallery.match(_emb(7), 0.5).name, "Camille")

    def test_rejects_blank_name(self) -> None:
        with self.assertRaises(ValueError):
            self.gallery.enroll("   ", [_emb(1)], [b"a"])

    def test_delete_unknown_and_bad_id(self) -> None:
        self.assertFalse(self.gallery.delete("does-not-exist"))
        self.assertIsNone(self.gallery.thumb_file("../etc/passwd"))
        self.assertFalse(self.gallery.delete("../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
