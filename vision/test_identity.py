"""Portillon d'identification : machine d'états, horloge factice."""

from __future__ import annotations

import unittest

from identity import AUTHORIZED, IDENTIFYING, IDLE, INTRUSION, IdentityConfig, IdentityMachine

CFG = IdentityConfig(window_s=8.0, known_hold_s=0.5, auth_grace_s=10.0, idle_s=5.0)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.events: list[str] = []

    def run(self, m: IdentityMachine, seconds: float, person: bool, status: str = "none",
            name: str | None = None, step: float = 0.3, sim: float | None = None, live: float | None = None) -> None:
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t = round(self.t + step, 3)
            self.events += [e.kind for e in m.update(self.t, person, status, name, sim, live)]


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = IdentityMachine(CFG)
        self.c = Clock()

    def test_person_starts_identification_once(self) -> None:
        self.c.run(self.m, 3.0, True, "checking")
        self.assertEqual(self.m.state, IDENTIFYING)
        self.assertEqual(self.c.events, ["identify_start"])
        self.assertAlmostEqual(self.m.remaining(self.c.t), 8.0 - 2.7, places=1)

    def test_known_live_within_window_authorizes(self) -> None:
        self.c.run(self.m, 2.0, True, "checking")
        self.c.run(self.m, 1.0, True, "known", "Guillaume")
        self.assertEqual(self.m.state, AUTHORIZED)
        self.assertEqual(self.m.snapshot(self.c.t)["name"], "Guillaume")
        self.assertEqual(self.c.events, ["identify_start", "identified"])

    def test_single_known_frame_is_not_enough(self) -> None:
        self.c.run(self.m, 1.0, True, "checking")
        self.c.run(self.m, 0.3, True, "known", "Guillaume")
        self.c.run(self.m, 1.0, True, "checking")
        self.assertEqual(self.m.state, IDENTIFYING)

    def test_window_expiry_unknown_is_intrusion(self) -> None:
        self.c.run(self.m, 9.0, True, "unknown", sim=0.21, live=0.97)
        self.assertEqual(self.m.state, INTRUSION)
        self.assertEqual(self.c.events, ["identify_start", "intrusion"])
        self.assertEqual(self.m.snapshot(self.c.t)["kind"], "unknown")

    def test_no_face_shown_is_intrusion(self) -> None:
        self.c.run(self.m, 9.0, True, "none")  # YOLO voit une personne, aucun visage
        self.assertEqual(self.c.events, ["identify_start", "intrusion"])

    def test_spoof_during_window_is_spoof_intrusion(self) -> None:
        self.c.run(self.m, 3.0, True, "spoof", live=0.02)
        self.c.run(self.m, 6.0, True, "checking")
        self.assertEqual(self.c.events, ["identify_start", "intrusion_spoof"])
        self.assertEqual(self.m.snapshot(self.c.t)["kind"], "spoof")

    def test_no_rebeep_for_continuous_presence(self) -> None:
        self.c.run(self.m, 30.0, True, "unknown")
        self.assertEqual(self.c.events, ["identify_start", "intrusion"])

    def test_back_to_idle_after_absence(self) -> None:
        self.c.run(self.m, 9.0, True, "unknown")
        self.c.run(self.m, 4.5, False)
        self.assertEqual(self.m.state, INTRUSION)  # pas encore 5 s
        self.c.run(self.m, 1.0, False)
        self.assertEqual(self.m.state, IDLE)
        self.c.run(self.m, 0.3, True, "checking")
        self.assertEqual(self.c.events, ["identify_start", "intrusion", "idle", "identify_start"])

    def test_brief_absence_keeps_window(self) -> None:
        self.c.run(self.m, 2.0, True, "checking")
        self.c.run(self.m, 1.5, False)
        self.c.run(self.m, 5.0, True, "unknown")
        self.assertEqual(self.c.events, ["identify_start", "intrusion"])

    def test_person_left_before_expiry_no_intrusion(self) -> None:
        self.c.run(self.m, 3.0, True, "unknown")
        self.c.run(self.m, 6.0, False)
        self.assertEqual(self.c.events, ["identify_start", "idle"])

    def test_authorized_stays_while_in_view_then_grace(self) -> None:
        self.c.run(self.m, 1.0, True, "known", "Guillaume")
        self.assertEqual(self.m.state, AUTHORIZED)
        self.c.run(self.m, 20.0, True, "known", "Guillaume")
        self.assertEqual(self.m.state, AUTHORIZED)
        self.c.run(self.m, 9.0, True, "unknown")  # visage connu perdu, < 10 s de grâce
        self.assertEqual(self.m.state, AUTHORIZED)
        self.c.run(self.m, 1.5, True, "unknown")
        self.assertEqual(self.m.state, IDENTIFYING)
        self.assertEqual(self.c.events, ["identify_start", "identified", "identify_start"])

    def test_authorized_person_leaving_goes_idle(self) -> None:
        self.c.run(self.m, 1.0, True, "known", "Guillaume")
        self.c.run(self.m, 6.0, False)
        self.assertEqual(self.m.state, IDLE)

    def test_intrusion_then_identified_clears(self) -> None:
        self.c.run(self.m, 9.0, True, "unknown")
        self.c.run(self.m, 1.0, True, "known", "Remy")
        self.assertEqual(self.m.state, AUTHORIZED)
        self.assertEqual(self.c.events, ["identify_start", "intrusion", "identified"])

    def test_info_carries_best_similarity_and_liveness(self) -> None:
        m = IdentityMachine(CFG)
        evs = []
        t = 0.0
        for sim in (0.1, 0.3, 0.2):
            t += 0.3
            evs += m.update(t, True, "unknown", None, sim, 0.95)
        evs += m.update(9.0, True, "unknown", None, 0.15, 0.9)
        intr = [e for e in evs if e.kind == "intrusion"][0]
        self.assertEqual(intr.info["similarity"], 0.3)
        self.assertEqual(intr.info["liveness"], 0.9)


if __name__ == "__main__":
    unittest.main()
