"""Décisions d'alarme visage inconnu, sans base ni MQTT."""

from __future__ import annotations

import unittest

from app.face_gate import IDENTITY_STATES, face_alarm_action, identity_action, identity_display, vision_camera_gated, vision_person_suppressed


class FaceGateTests(unittest.TestCase):
    def test_known_face_does_not_alarm(self) -> None:
        self.assertEqual(face_alarm_action(True, "face_known"), "clear")

    def test_unknown_alarms_only_when_enabled(self) -> None:
        self.assertEqual(face_alarm_action(True, "face_unknown"), "alarm")
        self.assertEqual(face_alarm_action(False, "face_unknown"), "ignore")

    def test_spoof_alarms_like_intrusion(self) -> None:
        self.assertEqual(face_alarm_action(True, "face_spoof"), "alarm")
        self.assertEqual(face_alarm_action(False, "face_spoof"), "ignore")

    def test_no_face_clears_without_alarm(self) -> None:
        self.assertEqual(face_alarm_action(True, "face_cleared"), "clear")
        self.assertEqual(face_alarm_action(True, "face_none"), "clear")

    def test_disabled_ignores_face_events(self) -> None:
        self.assertEqual(face_alarm_action(False, "face_known"), "ignore")
        self.assertEqual(face_alarm_action(False, "face_cleared"), "ignore")

    def test_person_camera_suppressed_when_face_gate_ready(self) -> None:
        self.assertTrue(vision_person_suppressed(True, True, "vision", "person_detected"))
        self.assertTrue(vision_person_suppressed(True, True, "vision_au_armement", "person_detected"))

    def test_person_camera_kept_when_gate_off_or_models_down(self) -> None:
        self.assertFalse(vision_person_suppressed(False, True, "vision", "person_detected"))
        self.assertFalse(vision_person_suppressed(True, False, "vision", "person_detected"))

    def test_pir_not_suppressed(self) -> None:
        self.assertFalse(vision_person_suppressed(True, True, "pir", "detected"))

    def test_clear_not_treated_as_person_trigger(self) -> None:
        self.assertFalse(vision_person_suppressed(True, True, "vision", "person_cleared"))

    def test_camera_clear_gated_with_face_alarm(self) -> None:
        self.assertTrue(vision_camera_gated(True, True, "vision", "person_cleared"))
        self.assertFalse(vision_camera_gated(True, True, "pir", "cleared"))
        self.assertFalse(vision_camera_gated(False, True, "vision", "person_detected"))


class IdentityGateTests(unittest.TestCase):
    def test_identify_start_beeps_even_when_disarmed(self) -> None:
        self.assertEqual(identity_action("identify_start", False, False), "identify_beep")

    def test_identified_confirms(self) -> None:
        self.assertEqual(identity_action("identified", True, False), "confirm")

    def test_intrusion_always_rings_even_when_disarmed(self) -> None:
        self.assertEqual(identity_action("intrusion", True, False), "alarm")
        self.assertEqual(identity_action("intrusion_spoof", False, True), "alarm")
        self.assertEqual(identity_action("intrusion", False, False), "alarm")
        self.assertEqual(identity_action("intrusion_spoof", False, False), "alarm")

    def test_identity_states(self) -> None:
        self.assertEqual(IDENTITY_STATES, {"identify_start", "identified", "intrusion", "intrusion_spoof", "identify_end"})
        self.assertEqual(identity_action("face_unknown", True, True), "ignore")

    def test_display_commands(self) -> None:
        self.assertEqual(identity_display("identify_start", None, 8000),
                         {"action": "display", "mode": "identify", "duration_ms": 8000})
        self.assertEqual(identity_display("identified", "Guillaume")["name"], "Guillaume")
        self.assertEqual(identity_display("intrusion_spoof")["mode"], "intrusion")
        self.assertEqual(identity_display("identify_end")["mode"], "normal")


if __name__ == "__main__":
    unittest.main()
