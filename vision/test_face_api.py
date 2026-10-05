"""Enrôlement : multipart et rejet sans visage."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

import numpy as np

from face_api import enroll_images, parse_multipart
from face_gallery import Gallery


def _emb() -> np.ndarray:
    v = np.ones(8, dtype=np.float32)
    return v / np.linalg.norm(v)


class _Det:
    def __init__(self) -> None:
        self.embedding = _emb()
        self.thumb_jpeg = b"thumb"


class _Embedder:
    def __init__(self, ready: bool = True, accept: bool = True) -> None:
        self.ready = ready
        self.accept = accept

    def embed_bytes(self, data: bytes):
        if not self.accept or data == b"noface":
            return None
        return _Det()


class ParseTests(unittest.TestCase):
    def test_name_and_photo(self) -> None:
        body = (
            b"--bound\r\n"
            b'Content-Disposition: form-data; name="name"\r\n\r\n'
            b"Alice\r\n"
            b"--bound\r\n"
            b'Content-Disposition: form-data; name="photo"; filename="a.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n"
            b"\xff\xd8jpeg\r\n"
            b"--bound--\r\n"
        )
        name, photos = parse_multipart("multipart/form-data; boundary=bound", body)
        self.assertEqual(name, "Alice")
        self.assertEqual(photos, [b"\xff\xd8jpeg"])


class EnrollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gallery = Gallery(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_enroll_known_vector(self) -> None:
        code, body = enroll_images(self.gallery, _Embedder(), "Alice", [b"img"])
        self.assertEqual(code, 201)
        self.assertEqual(body["name"], "Alice")
        self.assertEqual(self.gallery.match(_emb(), 0.5).status, "known")

    def test_no_face_rejected(self) -> None:
        code, body = enroll_images(self.gallery, _Embedder(accept=False), "Alice", [b"noface"])
        self.assertEqual(code, 422)
        self.assertIn("visage", body["error"])
        self.assertEqual(self.gallery.list_people(), [])

    def test_models_missing(self) -> None:
        code, _body = enroll_images(self.gallery, _Embedder(ready=False), "Alice", [b"img"])
        self.assertEqual(code, 503)

    def test_blank_name(self) -> None:
        code, _body = enroll_images(self.gallery, _Embedder(), "  ", [b"img"])
        self.assertEqual(code, 422)


if __name__ == "__main__":
    unittest.main()
