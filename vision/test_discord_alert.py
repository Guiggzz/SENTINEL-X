"""Alerte Discord : présence continue 2 s, cooldown 30 s, envoi sans fuite d'URL."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import discord_alert
from discord_alert import DwellNotifier, build_payload, post_discord


class DwellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.n = DwellNotifier(lambda k, i: self.sent.append((k, i)), dwell_s=2.0, cooldown_s=30.0, grace_s=1.0)

    def feed(self, status: str, t0: float, t1: float, step: float = 0.3) -> None:
        t = t0
        while t <= t1 + 1e-9:
            self.n.observe(status, {"similarity": 0.2, "liveness": 0.9}, t)
            t += step

    def test_no_alert_before_dwell(self) -> None:
        self.feed("unknown", 0.0, 1.8)
        self.assertEqual(self.sent, [])

    def test_alert_after_dwell_once_per_episode(self) -> None:
        self.feed("unknown", 0.0, 10.0)
        self.assertEqual([k for k, _ in self.sent], ["unknown"])

    def test_brief_appearances_never_alert(self) -> None:
        for start in (0.0, 5.0, 10.0):
            self.feed("unknown", start, start + 1.2)
            self.feed("none", start + 1.5, start + 4.5)
        self.assertEqual(self.sent, [])

    def test_short_gap_keeps_episode(self) -> None:
        self.feed("unknown", 0.0, 1.2)
        self.n.observe("none", {}, 1.5)       # visage perdu une passe
        self.feed("unknown", 1.8, 2.4)
        self.assertEqual(len(self.sent), 1)

    def test_cooldown_between_episodes(self) -> None:
        self.feed("unknown", 0.0, 3.0)
        self.feed("none", 3.3, 6.0)
        self.feed("unknown", 6.3, 12.0)        # nouvel épisode, mais < 30 s
        self.assertEqual(len(self.sent), 1)
        self.feed("none", 12.3, 40.0)
        self.feed("unknown", 40.3, 43.0)       # > 30 s après la 1re alerte
        self.assertEqual(len(self.sent), 2)

    def test_known_or_checking_never_alerts(self) -> None:
        self.feed("known", 0.0, 10.0)
        self.feed("checking", 10.3, 20.0)
        self.assertEqual(self.sent, [])

    def test_spoof_title_and_fields(self) -> None:
        self.feed("spoof", 0.0, 2.5)
        self.assertEqual(self.sent[0][0], "spoof")
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
        self.assertEqual(payload["embeds"][0]["title"], "Intrus détecté")
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
