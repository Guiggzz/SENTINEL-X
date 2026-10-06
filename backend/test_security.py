"""Tests de durcissement : validation des entrées, upload, IP client derrière proxy."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SESSION_SECRET", "test-secret")

from pydantic import ValidationError

from app.schemas import AlertIn, CommandIn
from app.faces import _is_image, validate_upload
from app import auth_security as sec

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _multipart(fields: list[tuple[str, bytes, str | None]]) -> tuple[str, bytes]:
    b = "XyZbOuNdArY"
    out = b""
    for name, value, filename in fields:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out += f"--{b}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + value + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return f"multipart/form-data; boundary={b}", out


class CommandValidation(unittest.TestCase):
    def test_valid(self):
        CommandIn(action="buzzer_on", duration_ms=10000, song="gaz")
        CommandIn(action="led", color="red", state=True)

    def test_rejects(self):
        bad = [
            {"action": "rm -rf"},
            {"action": "buzzer_on", "song": "hack"},
            {"action": "buzzer_on", "duration_ms": 999999},
            {"action": "beep", "device_id": "#"},
            {"action": "beep", "device_id": "a/b"},
            {"action": "beep", "extra": 1},
        ]
        for body in bad:
            with self.assertRaises(ValidationError, msg=body):
                CommandIn(**body)

    def test_alert_tokens(self):
        AlertIn(device_id="sentinel-node-01", type="vision", state="face_unknown")
        with self.assertRaises(ValidationError):
            AlertIn(device_id="x", type="<script>", state="on")


class UploadValidation(unittest.TestCase):
    def test_magic(self):
        self.assertTrue(_is_image(JPEG))
        self.assertTrue(_is_image(PNG))
        self.assertTrue(_is_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "))
        self.assertFalse(_is_image(b"GIF89a"))
        self.assertFalse(_is_image(b"<?php system($_GET[1]);"))

    def test_ok(self):
        ct, body = _multipart([("name", "Alice".encode(), None), ("photo", JPEG, "a.jpg")])
        self.assertIsNone(validate_upload(ct, body))

    def test_bad_type(self):
        ct, body = _multipart([("name", b"Alice", None), ("photo", b"GIF89a....", "a.jpg")])
        self.assertIn("format", validate_upload(ct, body))

    def test_bad_name(self):
        ct, body = _multipart([("name", b"<img src=x>", None), ("photo", JPEG, "a.jpg")])
        self.assertIn("nom", validate_upload(ct, body))

    def test_too_many(self):
        ct, body = _multipart([("name", b"Alice", None)] + [("photo", JPEG, "a.jpg")] * 6)
        self.assertIn("maximum", validate_upload(ct, body))

    def test_not_multipart(self):
        self.assertIsNotNone(validate_upload("application/json", b"{}"))


class _Req:
    def __init__(self, peer: str, xff: str | None = None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


class ClientIp(unittest.TestCase):
    def test_trusted_proxy_uses_last_hop(self):
        self.assertEqual(sec.client_ip(_Req("172.22.0.5", "1.2.3.4, 10.60.88.10")), "10.60.88.10")

    def test_untrusted_peer_ignores_xff(self):
        self.assertEqual(sec.client_ip(_Req("10.60.88.10", "1.1.1.1")), "10.60.88.10")


class Session(unittest.TestCase):
    def test_tamper_and_revoke(self):
        sec.SESSION_SECRET = "test-secret"
        tok = sec._sign("operateur:9999999999:nonce")
        self.assertEqual(sec._unsign(tok), "operateur")
        self.assertIsNone(sec._unsign(tok[:-1] + ("0" if tok[-1] != "0" else "1")))
        sec._revoked[tok] = 9999999999
        self.assertIsNone(sec._unsign(tok))


if __name__ == "__main__":
    unittest.main()
