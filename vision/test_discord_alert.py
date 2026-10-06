"""Alerte Discord : cooldown 30 s, titres, envoi multipart sans fuite d'URL."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import discord_alert
from discord_alert import Cooldown, build_payload, post_discord


class CooldownTests(unittest.TestCase):
    def test_cooldown_30s(self) -> None:
        c = Cooldown(30.0)
        self.assertTrue(c.allow(0.0))
        self.assertFalse(c.allow(10.0))
        self.assertFalse(c.allow(29.9))
        self.assertTrue(c.allow(30.0))

    def test_alerter_respects_cooldown_and_mocks_post(self) -> None:
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example/api/webhooks/1/S"}):
            a = discord_alert.DiscordAlerter(lambda: b"jpeg")
        sent = []
        a._send_async = lambda k, i: sent.append(k)  # pas de thread ni de réseau
        self.assertTrue(a.alert("unknown", {}, now=100.0))
        self.assertFalse(a.alert("spoof", {}, now=120.0))
        self.assertTrue(a.alert("spoof", {}, now=131.0))
        self.assertEqual(sent, ["unknown", "spoof"])

    def test_disabled_without_url(self) -> None:
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""}):
            a = discord_alert.DiscordAlerter(lambda: None)
        self.assertFalse(a.enabled)
        self.assertFalse(a.alert("unknown", {}, now=0.0))

    def test_titles_and_fields(self) -> None:
        p = build_payload("unknown", {"similarity": 0.21, "liveness": 0.97, "window_s": 8})
        self.assertEqual(p["embeds"][0]["title"], "Intrus détecté — non identifié après 8 s")
        payload = build_payload("spoof", {"similarity": 0.41, "liveness": 0.08, "camera": "sentinel-cam-01"})
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Leurre détecté (photo/écran)")
        names = [f["name"] for f in embed["fields"]]
        self.assertEqual(names, ["Heure (Europe/Paris)", "Similarité max", "Score vivant", "Caméra"])
        self.assertEqual(embed["fields"][2]["value"], "0.08")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


class _Resp:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class PostTests(unittest.TestCase):
    URL = "https://discord.example/api/webhooks/1/SECRET"

    def test_multipart_with_image(self) -> None:
        with mock.patch.object(discord_alert, "urlopen", return_value=_Resp()) as up:
            code = post_discord(self.URL, "unknown", {"similarity": 0.2, "liveness": 0.95}, b"\xff\xd8jpeg")
        self.assertEqual(code, 204)
        req = up.call_args[0][0]
        self.assertIn("multipart/form-data", req.get_header("Content-type"))
        self.assertIn(b'name="files[0]"; filename="camera.jpg"', req.data)
        payload = json.loads(req.data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
        self.assertTrue(payload["embeds"][0]["title"].startswith("Intrus détecté"))
        self.assertEqual(up.call_args[1]["timeout"], 5.0)

    def test_failure_logged_without_url(self) -> None:
        from urllib.error import URLError

        err = URLError(f"boom {self.URL}")
        with mock.patch.object(discord_alert, "urlopen", side_effect=err), \
                self.assertLogs("sentinel.discord", level="WARNING") as logs:
            self.assertIsNone(post_discord(self.URL, "unknown", {}, None))
        self.assertNotIn("SECRET", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
