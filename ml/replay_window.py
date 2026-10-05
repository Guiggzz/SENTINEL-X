"""Rejoue une plage de telemetrie reelle (heure UTC) avec le modele courant et affiche les transitions d'etat.
usage : python replay_window.py 2026-10-05T12:10:00 2026-10-05T12:40:00 [device]"""
import sys
import pandas as pd
from detector import Model
from train import load_db, stream
import os

start, end = pd.Timestamp(sys.argv[1], tz="UTC"), pd.Timestamp(sys.argv[2], tz="UTC")
dev = sys.argv[3] if len(sys.argv) > 3 else "sentinel-node-01"
df = load_db(dev)
seg = df[(df["created_at"] >= start) & (df["created_at"] <= end)].reset_index(drop=True)
out = stream(Model(os.environ.get("ML_MODEL_DIR", "/app/models")), seg)
prev = None
for i, r in out.iterrows():
    if r["state"] != prev:
        ts = seg.loc[i, "created_at"].tz_convert("Europe/Paris").strftime("%H:%M:%S")
        print(f"{ts} {r['state']:<10} risk={r['risk']:5.1f} gas={seg.loc[i,'gas']} t={seg.loc[i,'temperature']} h={seg.loc[i,'humidity']} label={r['label']}")
        prev = r["state"]
