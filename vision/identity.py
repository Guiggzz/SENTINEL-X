"""Machine d'états d'identification (portillon) — sans micro, bips via le buzzer de l'ESP.

IDLE --personne--> IDENTIFYING (bip « identifiez-vous », fenêtre de N s)
IDENTIFYING --visage connu ET vivant stable--> AUTHORIZED (« Autorisé : <nom> »)
IDENTIFYING --fenêtre expirée (inconnu, pas de visage, leurre)--> INTRUSION (sirène si armé + Discord)
AUTHORIZED --connu perdu depuis grace_s, personne encore là--> IDENTIFYING (nouveau bip)
INTRUSION  --connu ET vivant--> AUTHORIZED (coupe la sirène)
tout état --plus personne depuis idle_s--> IDLE (pas de nouveau bip pour une présence continue)

Pure (horloge passée en argument) : testée avec une horloge factice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

IDLE, IDENTIFYING, AUTHORIZED, INTRUSION = "idle", "identifying", "authorized", "intrusion"


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class IdentityConfig:
    enabled: bool = True
    window_s: float = 8.0       # temps laissé pour s'identifier
    known_hold_s: float = 0.5   # « connu + vivant » doit tenir ce temps
    auth_grace_s: float = 10.0  # reste autorisé N s après la dernière vue du visage connu
    idle_s: float = 5.0         # plus personne depuis N s -> retour au repos
    present_s: float = 1.5      # intrusion seulement si quelqu'un est encore là à l'expiration

    @classmethod
    def from_env(cls) -> "IdentityConfig":
        return cls(
            enabled=os.environ.get("SENTINEL_IDENTIFY", "1").strip().lower() not in ("0", "false", "no", "off"),
            window_s=_f("SENTINEL_IDENTIFY_WINDOW_S", cls.window_s),
            known_hold_s=_f("SENTINEL_IDENTIFY_HOLD_S", cls.known_hold_s),
            auth_grace_s=_f("SENTINEL_IDENTIFY_GRACE_S", cls.auth_grace_s),
            idle_s=_f("SENTINEL_IDENTIFY_IDLE_S", cls.idle_s),
        )


@dataclass
class Event:
    kind: str                   # identify_start | identified | intrusion | intrusion_spoof | idle
    name: str | None = None
    info: dict = field(default_factory=dict)


class IdentityMachine:
    def __init__(self, cfg: IdentityConfig) -> None:
        self.cfg = cfg
        self.state = IDLE
        self.since = 0.0
        self.name: str | None = None
        self.intrusion_kind: str | None = None
        self._last_person: float | None = None
        self._known_since: float | None = None
        self._last_known: float | None = None
        self._spoof_seen = False
        self._best_sim: float | None = None
        self._last_live: float | None = None

    def _goto(self, state: str, now: float) -> None:
        self.state = state
        self.since = now

    def remaining(self, now: float) -> float | None:
        if self.state != IDENTIFYING:
            return None
        return max(0.0, self.cfg.window_s - (now - self.since))

    def snapshot(self, now: float) -> dict:
        rem = self.remaining(now)
        return {
            "state": self.state,
            "name": self.name if self.state == AUTHORIZED else None,
            "remaining_s": None if rem is None else round(rem, 1),
            "window_s": self.cfg.window_s,
            "kind": self.intrusion_kind if self.state == INTRUSION else None,
            "since_s": round(now - self.since, 1) if self.state != IDLE else None,
        }

    def update(
        self,
        now: float,
        person: bool,
        status: str,
        name: str | None = None,
        similarity: float | None = None,
        liveness: float | None = None,
    ) -> list[Event]:
        """status = statut visage agrégé : none | known | unknown | spoof | checking.

        « known » implique déjà la preuve de vivacité (voir face_liveness)."""
        events: list[Event] = []
        present = person or status not in ("none", "unavailable", "")
        if present:
            self._last_person = now
        if status == "known" and name:
            if self._known_since is None:
                self._known_since = now
            self._last_known = now
        else:
            self._known_since = None
        known_ok = self._known_since is not None and now - self._known_since >= self.cfg.known_hold_s
        if status == "spoof":
            self._spoof_seen = True
        if similarity is not None:
            self._best_sim = similarity if self._best_sim is None else max(self._best_sim, similarity)
        if liveness is not None:
            self._last_live = liveness
        info = {"similarity": self._best_sim, "liveness": self._last_live}

        absent_long = self._last_person is None or now - self._last_person >= self.cfg.idle_s
        if self.state != IDLE and absent_long and not present:
            self._reset(now)
            events.append(Event("idle"))
            return events

        if self.state == IDLE:
            if present:
                self._start(now)
                events.append(Event("identify_start"))
                if known_ok:  # déjà reconnu à l'arrivée
                    self._authorize(now, name)
                    events.append(Event("identified", name, info))
            return events

        if self.state == IDENTIFYING:
            if known_ok:
                self._authorize(now, name)
                events.append(Event("identified", name, info))
            elif now - self.since >= self.cfg.window_s and now - (self._last_person or -1e9) <= self.cfg.present_s:
                self.intrusion_kind = "spoof" if self._spoof_seen else "unknown"
                self._goto(INTRUSION, now)
                events.append(Event("intrusion_spoof" if self._spoof_seen else "intrusion", None, info))
            return events

        if self.state == AUTHORIZED:
            if status == "known" and name:
                self.name = name
            if self._last_known is not None and now - self._last_known >= self.cfg.auth_grace_s and present:
                self._start(now)
                events.append(Event("identify_start"))
            return events

        if self.state == INTRUSION and known_ok:
            self._authorize(now, name)
            events.append(Event("identified", name, info))
        return events

    def _start(self, now: float) -> None:
        self._goto(IDENTIFYING, now)
        self.name = None
        self.intrusion_kind = None
        self._spoof_seen = False
        self._best_sim = None
        self._last_live = None

    def _authorize(self, now: float, name: str | None) -> None:
        self._goto(AUTHORIZED, now)
        self.name = name
        self.intrusion_kind = None
        self._last_known = now

    def _reset(self, now: float) -> None:
        self._goto(IDLE, now)
        self.name = None
        self.intrusion_kind = None
        self._known_since = None
        self._last_known = None
        self._spoof_seen = False
        self._best_sim = None
        self._last_live = None
