"""Notification Discord d'intrusion : personne non identifiée à la fin de la fenêtre
d'identification (visage inconnu, pas de visage, ou leurre photo/écran).

L'URL du webhook est un secret : lue dans l'environnement (DISCORD_WEBHOOK_URL, fichier
~/.config/sentinel/vision.env chargé par l'unité systemd), jamais écrite dans les logs.
L'envoi part dans un thread (timeout 5 s) pour ne pas ralentir la boucle vision.
Indépendant de l'armement : c'est une notification ; la sirène, elle, suit l'interrupteur.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo

    _PARIS = ZoneInfo("Europe/Paris")
except Exception:  # noqa: BLE001
    _PARIS = None

logger = logging.getLogger("sentinel.discord")

ALARM_KINDS = ("unknown", "spoof")
TITLES = {
    "unknown": "Intrus détecté — non identifié après {window} s",
    "spoof": "Leurre détecté (photo/écran)",
    "test": "Test SENTINEL-X — alerte intrus",
}
DESCRIPTIONS = {
    "unknown": "Personne devant la caméra, non identifiée à la fin de la fenêtre d'identification.",
    "spoof": "Visage présenté sur une photo ou un écran (anti-spoofing). Tentative d'usurpation.",
    "test": "Message de test : vérification du webhook d'alerte.",
}
COLORS = {"unknown": 0xA33A2E, "spoof": 0xC45C26, "test": 0x6B6A64}


class Cooldown:
    """Au plus une alerte toutes les cooldown_s secondes (anti-spam Discord)."""

    def __init__(self, cooldown_s: float = 30.0) -> None:
        self.cooldown_s = cooldown_s
        self._last: float | None = None

    def allow(self, now: float) -> bool:
        if self._last is not None and now - self._last < self.cooldown_s:
            return False
        self._last = now
        return True


def _fmt(value, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def build_payload(kind: str, info: dict, when: datetime | None = None) -> dict:
    when = when or datetime.now(timezone.utc)
    local = when.astimezone(_PARIS) if _PARIS else when
    fields = [
        {"name": "Heure (Europe/Paris)", "value": local.strftime("%d/%m/%Y %H:%M:%S"), "inline": True},
        {"name": "Similarité max", "value": _fmt(info.get("similarity")), "inline": True},
        {"name": "Score vivant", "value": _fmt(info.get("liveness")), "inline": True},
        {"name": "Caméra", "value": str(info.get("camera") or "sentinel-cam-01"), "inline": True},
    ]
    embed = {
        "title": TITLES.get(kind, TITLES["unknown"]).format(window=_fmt(info.get("window_s", 8), 0)),
        "description": DESCRIPTIONS.get(kind, ""),
        "color": COLORS.get(kind, COLORS["unknown"]),
        "fields": fields,
        "timestamp": when.astimezone(timezone.utc).isoformat(),
        "footer": {"text": "SENTINEL-X · vision"},
        "image": {"url": "attachment://camera.jpg"},
    }
    return {"username": "SENTINEL-X", "embeds": [embed], "allowed_mentions": {"parse": []}}


def _multipart(payload: dict, jpeg: bytes | None) -> tuple[bytes, str]:
    boundary = "sx" + secrets.token_hex(12)
    if not jpeg:
        payload["embeds"][0].pop("image", None)
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        "Content-Type: application/json\r\n\r\n".encode() + json.dumps(payload).encode() + b"\r\n"
    ]
    if jpeg:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"camera.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n".encode() + jpeg + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _scrub(text: str, url: str) -> str:
    text = str(text)
    if url:
        text = text.replace(url, "<webhook>")
    return text.replace("discord.com/api/webhooks", "<webhook>").replace("discordapp.com/api/webhooks", "<webhook>")


def post_discord(url: str, kind: str, info: dict, jpeg: bytes | None, timeout: float = 5.0) -> int | None:
    """POST multipart ; retourne le code HTTP (200/204 = ok) ou None. Ne logge jamais l'URL."""
    if not url:
        return None
    body, ctype = _multipart(build_payload(kind, info), jpeg)
    req = Request(
        url,
        data=body,
        headers={"Content-Type": ctype, "User-Agent": "DiscordBot (sentinel-x, 1.0)"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL de config, https)
            return int(resp.status)
    except HTTPError as exc:
        logger.warning("Discord: HTTP %s", exc.code)
        return int(exc.code)
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("Discord: envoi impossible (%s)", _scrub(getattr(exc, "reason", exc), url))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Discord: erreur %s (%s)", type(exc).__name__, _scrub(exc, url))
    return None


class DiscordAlerter:
    """Envoi en arrière-plan avec cooldown. get_jpeg fournit la trame annotée (cadres)."""

    def __init__(self, get_jpeg: Callable[[], bytes | None], camera: str = "sentinel-cam-01") -> None:
        self.url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        flag = os.environ.get("DISCORD_ALERTS_ENABLED", "").strip().lower()
        self.enabled = bool(self.url) and flag not in ("0", "false", "no", "off")
        if self.url and not self.url.startswith("https://"):
            logger.warning("Discord: URL de webhook non https, alertes désactivées")
            self.enabled = False
        self.camera = camera
        self.timeout = float(os.environ.get("DISCORD_TIMEOUT_S", "5"))
        self._get_jpeg = get_jpeg
        self.cooldown = Cooldown(float(os.environ.get("DISCORD_COOLDOWN_S", "30")))
        self.last_result: dict | None = None

    def alert(self, kind: str, info: dict, now: float | None = None, jpeg: bytes | None = None) -> bool:
        """kind : unknown | spoof. Retourne True si un envoi part (cooldown respecté).

        jpeg : capture prise à la détection de la personne (début de fenêtre) ; à défaut,
        trame courante."""
        if not self.enabled:
            return False
        if not self.cooldown.allow(time.monotonic() if now is None else now):
            logger.info("Discord: alerte %s ignorée (cooldown)", kind)
            return False
        self._send_async(kind, info, jpeg)
        return True

    def _send_async(self, kind: str, info: dict, jpeg: bytes | None = None) -> None:
        info.setdefault("camera", self.camera)
        threading.Thread(target=self._send, args=(kind, info, jpeg), name="discord-alert", daemon=True).start()

    def _send(self, kind: str, info: dict, jpeg: bytes | None = None) -> None:
        if not jpeg:
            time.sleep(0.2)  # laisse passer une trame annotée avec l'étiquette du visage courant
            try:
                jpeg = self._get_jpeg()
            except Exception:  # noqa: BLE001
                jpeg = None
        code = post_discord(self.url, kind, info, jpeg, timeout=self.timeout)
        ok = code in (200, 204)
        self.last_result = {"kind": kind, "ok": ok, "status": code, "at": datetime.now(timezone.utc).isoformat()}
        if ok:
            logger.info("Discord: alerte %s envoyée", kind)
        else:
            logger.warning("Discord: alerte %s non envoyée (code %s)", kind, code)


def send_test(jpeg: bytes | None, camera: str = "sentinel-cam-01") -> int | None:
    """Envoie le message de test (python3 discord_alert.py --test)."""
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    return post_discord(url, "test", {"camera": camera, "similarity": None, "liveness": None}, jpeg)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if "--test" not in sys.argv:
        print("usage: DISCORD_WEBHOOK_URL=... python3 discord_alert.py --test [image.jpg]")
        raise SystemExit(2)
    img = None
    rest = [a for a in sys.argv[1:] if a != "--test"]
    if rest:
        with open(rest[0], "rb") as fh:
            img = fh.read()
    status = send_test(img)
    print(f"HTTP {status}")
    raise SystemExit(0 if status in (200, 204) else 1)
