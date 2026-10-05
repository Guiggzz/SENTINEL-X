#!/usr/bin/env python3
"""Télécharge une fois les modèles OpenCV YuNet + SFace (hors git, ~40 Mo).

Usage :
    python3 fetch_face_models.py
Les fichiers vont dans vision/models/. Sans eux, YOLO continue et la
reconnaissance reste « indisponible ».
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "models"

MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}

MIN_BYTES = {
    "face_detection_yunet_2023mar.onnx": 200_000,
    "face_recognition_sface_2021dec.onnx": 10_000_000,
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        target = DEST / name
        if target.is_file() and target.stat().st_size >= MIN_BYTES[name]:
            print(f"déjà présent {target} ({target.stat().st_size} o)")
            continue
        print(f"téléchargement {name} …")
        try:
            urllib.request.urlretrieve(url, target)
        except OSError as exc:
            print(f"échec {name}: {exc}", file=sys.stderr)
            return 1
        size = target.stat().st_size
        if size < MIN_BYTES[name]:
            print(f"fichier trop petit ({size} o) : {target}", file=sys.stderr)
            return 1
        print(f"ok {target} ({size} o)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
