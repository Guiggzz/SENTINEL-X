"""Règles pures : visage inconnu ou leurre sonne, visage connu non, portillon caméra."""

from __future__ import annotations

_PERSON_TRIG = {"person_detected", "detected", "present", "on"}
_PERSON_CLEAR = {"person_cleared", "cleared", "absent", "off"}


def face_alarm_action(face_alarm_on: bool, state: str) -> str:
    """alarm | clear | ignore.

    clear ne coupe la sirène que si c'est elle qui l'a lancée (voir extras).
    """
    if not face_alarm_on:
        return "ignore"
    st = (state or "").lower()
    if st in {"face_unknown", "face_spoof"}:  # leurre (photo/écran) = tentative d'intrusion
        return "alarm"
    if st in {"face_cleared", "face_known", "face_none"}:
        return "clear"
    return "ignore"


def vision_camera_gated(face_alarm_on: bool, face_ready: bool, source: str, state: str) -> bool:
    """Caméra YOLO ignorée (déclenchement et fin) quand le portillon visage est opérationnel."""
    if not face_alarm_on or not face_ready:
        return False
    if not (source or "").startswith("vision"):
        return False
    st = (state or "").lower()
    return st in _PERSON_TRIG or st in _PERSON_CLEAR


def vision_person_suppressed(face_alarm_on: bool, face_ready: bool, source: str, state: str) -> bool:
    """Quand le portillon est armé et les modèles prêts, une simple personne YOLO ne sonne pas.

    Le PIR n'est pas concerné. Visage connu → pas d'alarme. Visage inconnu passe par face_unknown.
    """
    if not face_alarm_on or not face_ready:
        return False
    if not (source or "").startswith("vision"):
        return False
    return (state or "").lower() in _PERSON_TRIG
