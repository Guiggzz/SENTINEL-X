"""Décisions d'alarme visage inconnu, sans base ni MQTT."""

from __future__ import annotations

import unittest

from app.face_gate import face_alarm_action, vision_camera_gated, vision_person_suppressed


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


if __name__ == "__main__":
    unittest.main()
