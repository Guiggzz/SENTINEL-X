#!/usr/bin/env python3
"""Télécharge une fois les modèles visage (hors git, ~43 Mo) :

- OpenCV YuNet (détection) + SFace (empreinte), opencv_zoo ;
- anti-spoofing Silent-Face-Anti-Spoofing (Minivision, Apache-2.0) : MiniFASNetV2 (2.7)
  et MiniFASNetV1SE (4.0), conversions ONNX de QingHeYang/Silent-Face-Anti-Spoofing-onnx
  (commit épinglé), contrôlées par SHA-256 et vérifiées équivalentes aux poids .pth
  officiels (sorties identiques à 1e-6 près).

Usage :
    python3 fetch_face_models.py
Les fichiers vont dans vision/models/. Sans eux, YOLO continue et la
reconnaissance (ou l'anti-spoofing) reste « indisponible ».
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "models"

_FAS = (
    "https://raw.githubusercontent.com/QingHeYang/Silent-Face-Anti-Spoofing-onnx/"
    "584d4421d7ac42c59e640796f46e886b0095367a/onnx/"
)

MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
    "2.7_80x80_MiniFASNetV2.onnx": _FAS + "2.7_80x80_MiniFASNetV2.onnx",
    "4_0_0_80x80_MiniFASNetV1SE.onnx": _FAS + "4_0_0_80x80_MiniFASNetV1SE.onnx",
}

MIN_BYTES = {
    "face_detection_yunet_2023mar.onnx": 200_000,
    "face_recognition_sface_2021dec.onnx": 10_000_000,
    "2.7_80x80_MiniFASNetV2.onnx": 1_700_000,
    "4_0_0_80x80_MiniFASNetV1SE.onnx": 1_700_000,
}

# Empreintes exigées (anti-spoofing : fichier tiers, on ne fait confiance qu'au hash)
SHA256 = {
    "2.7_80x80_MiniFASNetV2.onnx": "0cbe5caec95c31de9d2ef845cb85407d76aecd1b6a2c0e343f7d35306bfbccb8",
    "4_0_0_80x80_MiniFASNetV1SE.onnx": "a25886a85cdcfa2c4ea23edb71de35f250c17827b4cadd253a972b28c80fdf1e",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ok(name: str, target: Path) -> bool:
    if not target.is_file() or target.stat().st_size < MIN_BYTES[name]:
        return False
    return name not in SHA256 or _sha256(target) == SHA256[name]


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in MODELS.items():
        target = DEST / name
        if _ok(name, target):
            print(f"déjà présent {target} ({target.stat().st_size} o)")
            continue
        print(f"téléchargement {name} …")
        tmp = target.with_suffix(".part")
        try:
            urllib.request.urlretrieve(url, tmp)
        except OSError as exc:
            print(f"échec {name}: {exc}", file=sys.stderr)
            return 1
        size = tmp.stat().st_size
        if size < MIN_BYTES[name]:
            tmp.unlink(missing_ok=True)
            print(f"fichier trop petit ({size} o) : {name}", file=sys.stderr)
            return 1
        if name in SHA256 and _sha256(tmp) != SHA256[name]:
            tmp.unlink(missing_ok=True)
            print(f"SHA-256 inattendu pour {name} : fichier rejeté", file=sys.stderr)
            return 1
        tmp.replace(target)
        print(f"ok {target} ({size} o)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
