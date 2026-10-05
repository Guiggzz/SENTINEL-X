"""SENTINEL-X - entrainement + evaluation du modele de maintenance predictive.

Usage (depuis infra/) :
    docker compose run --rm sentinel-ml python train.py            # entraine + evalue
    docker compose run --rm sentinel-ml python train.py --eval-only
Options : --csv fichier.csv (colonnes created_at,gas,temperature,humidity) au lieu de Postgres.

Donnees : historique "normal" de la table telemetry (device ML_TRAIN_DEVICE) lu dans Postgres.
Sorties (ML_MODEL_DIR, defaut ./models) : model.joblib (scaler + IsolationForest),
metadata.json (taille d'entrainement, contamination, features, date, calibration),
et ../evaluation.md (ou ML_EVAL_FILE) avec les resultats sur donnees normales et incidents simules.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from features import DT_S, FEATURES, W_LONG, FeatureState, vectorize
from detector import Model, IncidentTracker

SEED = 42
CONTAMINATION = float(os.getenv("ML_CONTAMINATION", "0.005"))
N_ESTIMATORS = 300
MAX_GAP_S = 60.0            # au-dela : nouvelle sequence (ESP redemarre / coupure)
HOLDOUT = 0.2               # 20 % les plus recents gardes pour l'evaluation (split temporel)
AUG_GAS_NOISE = float(os.getenv("ML_AUG_GAS_NOISE", "10"))   # MQ-2 : bruit ADC typique +-10 counts
AUG_TEMP_NOISE = 0.5   # DHT22 : precision +-0.5 C (resolution 0.1 C appliquee apres)
AUG_HUM_NOISE = 2.0    # DHT22 : precision +-2 %RH
# Plancher des echelles (resolution / bruit capteur) : evite qu'apres projection
# directionnelle (beaucoup de 0) le modele et les z-scores deviennent hypersensibles.
SCALE_FLOOR = {
    "gas_dev_rel": 10.0 / 70.0,    # +-10 ADC / baseline typique ~70
    "gas_slope_30s": 10.0 / 70.0,
    "gas_std_30s": 5.0 / 70.0,
    "gas_slope_2m": 5.0 / 70.0,
    "temp_slope_2m": 0.25,         # ~0.5 C sur 2 min
    "temp_dev": 0.5,               # precision DHT22
    "hum_slope_2m": 1.0,           # %/min (amplitude de chute)
    "gas_temp_corr": 0.2,
}
MODEL_DIR = Path(os.getenv("ML_MODEL_DIR", Path(__file__).resolve().parent / "models"))
EVAL_FILE = Path(os.getenv("ML_EVAL_FILE", MODEL_DIR.parent / "evaluation.md"))


# ---------------------------------------------------------------- donnees
def db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        url = "postgresql://{u}:{p}@{h}:5432/{d}".format(
            u=os.getenv("POSTGRES_USER", "sentinel"), p=os.getenv("POSTGRES_PASSWORD", "sentinel"),
            h=os.getenv("POSTGRES_HOST", "sentinel-db"), d=os.getenv("POSTGRES_DB", "sentinel"))
    return url.replace("postgresql+asyncpg://", "postgresql://")


def load_db(device: str) -> pd.DataFrame:
    import psycopg
    with psycopg.connect(db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, gas, temperature, humidity FROM telemetry "
            "WHERE device_id = %s AND gas IS NOT NULL ORDER BY created_at", (device,))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["created_at", "gas", "temperature", "humidity"])
    # Telemetrie a 0,5 s depuis le 05/10 : on ramene au pas de 2 s (fenetres du modele en echantillons)
    if len(df):
        bucket = pd.to_datetime(df["created_at"], utc=True).dt.floor("2s")
        df = df.groupby(bucket, sort=True).first().reset_index(drop=True)
    return df


EXCL_FILE = Path(os.getenv("ML_EXCLUSIONS", Path(__file__).resolve().parent / "training_exclusions.json"))


def load_exclusions() -> list[dict]:
    if not EXCL_FILE.exists():
        return []
    return json.loads(EXCL_FILE.read_text()).get("periods", [])


def apply_exclusions(df: pd.DataFrame, periods: list[dict]) -> pd.DataFrame:
    """Retire les periodes etiquetees non normales ; insere une coupure (gap) a leur place."""
    t = pd.to_datetime(df["created_at"], utc=True)
    keep = pd.Series(True, index=df.index)
    for p in periods:
        keep &= ~((t >= pd.Timestamp(p["start"])) & (t < pd.Timestamp(p["end"])))
    return df[keep].reset_index(drop=True)


def segments(df: pd.DataFrame) -> list[pd.DataFrame]:
    t = pd.to_datetime(df["created_at"], utc=True)
    cut = (t.diff().dt.total_seconds().fillna(0) > MAX_GAP_S).cumsum()
    return [g.reset_index(drop=True) for _, g in df.groupby(cut) if len(g) > W_LONG + 30]


def seq_features(seg: pd.DataFrame) -> np.ndarray:
    fs = FeatureState()
    out = []
    for g, t, h in zip(seg["gas"], seg["temperature"], seg["humidity"]):
        f = fs.update(g, None if pd.isna(t) else t, None if pd.isna(h) else h)
        if f is not None:
            out.append(vectorize(f))
    return np.array(out).reshape(-1, len(FEATURES))


def jitter(seg: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Bruit capteur realiste : MQ-2 +-10 ADC, DHT22 +-0.5 C (arrondi 0.1) et +-2 %RH."""
    s = seg.copy()
    s["gas"] = s["gas"].astype(float) + rng.uniform(-AUG_GAS_NOISE, AUG_GAS_NOISE, len(s))
    s["temperature"] = np.round(
        s["temperature"].astype(float) + rng.uniform(-AUG_TEMP_NOISE, AUG_TEMP_NOISE, len(s)), 1)
    s["humidity"] = s["humidity"].astype(float) + rng.uniform(-AUG_HUM_NOISE, AUG_HUM_NOISE, len(s))
    return s


def person_nearby(seg: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Presence humaine benigne (normale) : respiration (+2..8 %RH) + legere hausse lente (+0.3..0.8 C).

    Doit rester dans le domaine 'normal' appris : un vrai essai chauffage (+2 C en 1-2 min)
    reste bien au-dela de ces amplitudes.
    """
    s = jitter(seg, rng)
    n = len(s)
    if n < W_LONG + 40:
        return s
    dur = int(rng.integers(30, 91))          # 1 a 3 min
    at = int(rng.integers(W_LONG, max(W_LONG + 1, n - dur - 1)))
    dT = float(rng.uniform(0.3, 0.8))
    dH = float(rng.uniform(2.0, 8.0))
    end = min(n, at + dur)
    rise = max(1, dur // 3)
    for i, idx in enumerate(range(at, end)):
        if i < rise:
            f = (i + 1) / rise
        elif i < dur - rise:
            f = 1.0
        else:
            f = max(0.0, 1.0 - (i - (dur - rise)) / rise)
        s.loc[idx, "temperature"] = float(s.loc[idx, "temperature"]) + dT * f
        s.loc[idx, "humidity"] = float(s.loc[idx, "humidity"]) + dH * f
    return s


# ---------------------------------------------------------------- incidents synthetiques
def feature_jitter(X: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Bruit directionnel dans l'espace des features, calibre sur la resolution capteur.

    Apres vectorize(), beaucoup de coordonnees sont a 0 (sens non dangereux). Sans ce
    bruit, la variance d'entrainement est artificiellement basse et la foret sur-reagit
    a la moindre micro-hausse (quelques counts ADC).
    """
    floors = np.array([SCALE_FLOOR[f] for f in FEATURES], dtype=float)
    out = X + rng.normal(0.0, floors, X.shape)
    for i, f in enumerate(FEATURES):
        if f == "hum_slope_2m":
            out[:, i] = np.minimum(out[:, i], 0.0)
        else:
            out[:, i] = np.maximum(out[:, i], 0.0)
    return out


def inject_butane(seg: pd.DataFrame, at: int, peak: float, rng) -> tuple[pd.DataFrame, int]:
    """Spray de briquet pres du MQ-2 : saut a 'peak' en 2-3 echantillons, plateau 10-30 s, decroissance ~60 s."""
    s = seg.copy()
    s["gas"] = s["gas"].astype(float)
    base = float(s["gas"].iloc[max(0, at - 30):at].mean())
    rise = int(rng.integers(1, 3))
    hold = int(rng.integers(5, 16))
    tau = rng.uniform(10, 25)
    n = len(s)
    for k in range(0, n - at):
        if k < rise:
            v = base + (peak - base) * (k + 1) / rise
        elif k < rise + hold:
            v = peak + rng.normal(0, 0.05 * peak)
        else:
            v = base + (peak - base) * np.exp(-(k - rise - hold) / tau)
        if v - base < 2:
            break
        s.loc[at + k, "gas"] = v
    return s, at


def inject_leak_ramp(seg: pd.DataFrame, at: int, rate_per_min: float) -> pd.DataFrame:
    """Fuite lente : derive lineaire du gaz (rate_per_min unites ADC / min)."""
    s = seg.copy()
    s["gas"] = s["gas"].astype(float)
    k = np.arange(len(s) - at)
    s.loc[at:, "gas"] = s.loc[at:, "gas"].to_numpy() + rate_per_min * k * DT_S / 60.0
    return s


def inject_heat(seg: pd.DataFrame, at: int, degc_per_min: float, gas_per_min: float) -> pd.DataFrame:
    """Echauffement lent (+x degC/min) correle a une micro-deviation de gaz (+y ADC/min)."""
    s = seg.copy()
    s["gas"] = s["gas"].astype(float)
    s["temperature"] = s["temperature"].astype(float)
    s["humidity"] = s["humidity"].astype(float)
    k = np.arange(len(s) - at) * DT_S / 60.0
    s.loc[at:, "temperature"] = s.loc[at:, "temperature"].to_numpy() + degc_per_min * k
    s.loc[at:, "gas"] = s.loc[at:, "gas"].to_numpy() + gas_per_min * k
    s.loc[at:, "humidity"] = s.loc[at:, "humidity"].to_numpy() - 0.6 * degc_per_min * k
    return s


def tile(seg: pd.DataFrame, n: int) -> pd.DataFrame:
    """Prolonge une sequence normale par miroirs successifs (pas de discontinuite aux raccords)."""
    parts, k = [], 0
    while sum(len(p) for p in parts) < n:
        parts.append(seg if k % 2 == 0 else seg.iloc[::-1])
        k += 1
    out = pd.concat(parts).iloc[:n].reset_index(drop=True)
    out["created_at"] = range(n)
    return out


def stream(model: Model, seg: pd.DataFrame) -> pd.DataFrame:
    """Rejoue une sequence comme le service temps reel (gel de la ligne de base en anomalie)."""
    fs, tr = FeatureState(), IncidentTracker()
    rows = []
    for i, (g, t, h) in enumerate(zip(seg["gas"], seg["temperature"], seg["humidity"])):
        freeze = tr.state not in ("normal", "warmup")
        f = fs.update(g, None if pd.isna(t) else t, None if pd.isna(h) else h, freeze=freeze)
        if f is None:
            rows.append((i, np.nan, 0.0, False, "warmup", ""))
            continue
        x = vectorize(f)
        s = float(model.score(x)[0])
        anom = s > model.s_thr
        lab, _ = model.label(x)
        st = tr.step(float(model.risk(s)), anom, lab)
        rows.append((i, s, float(model.risk(s)), anom, st, lab))
    return pd.DataFrame(rows, columns=["i", "score", "risk", "anom", "state", "label"])


def first(df: pd.DataFrame, cond, start: int) -> int | None:
    idx = df.index[(df["i"] >= start) & cond]
    return int(df.loc[idx[0], "i"]) if len(idx) else None


# ---------------------------------------------------------------- entrainement
def train(df: pd.DataFrame, device: str) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    segs = segments(apply_exclusions(df, load_exclusions()))
    if not segs:
        sys.exit("Pas assez de telemetrie continue pour entrainer.")
    # split temporel : les 20 % d'echantillons les plus recents -> evaluation
    total = sum(len(s) for s in segs)
    cut_n, acc, train_segs, test_segs = int(total * (1 - HOLDOUT)), 0, [], []
    for s in segs:
        if acc + len(s) <= cut_n:
            train_segs.append(s)
        elif acc >= cut_n:
            test_segs.append(s)
        else:
            k = cut_n - acc
            if k > W_LONG + 30:
                train_segs.append(s.iloc[:k].reset_index(drop=True))
            if len(s) - k > W_LONG + 30:
                test_segs.append(s.iloc[k:].reset_index(drop=True))
        acc += len(s)

    rng = np.random.default_rng(SEED)
    X_real = np.vstack([seq_features(s) for s in train_segs])
    # double augmentation : bruit capteur + scenarios 'personne proche' (toujours labels normaux)
    X_jitter = np.vstack([seq_features(jitter(s, rng)) for s in train_segs]) if AUG_GAS_NOISE > 0 else np.empty((0, len(FEATURES)))
    X_person = np.vstack([seq_features(person_nearby(s, rng)) for s in train_segs])
    X_aug = np.vstack([X_jitter, X_person]) if len(X_jitter) else X_person
    # bruit additionnel dans l'espace features (plancher resolution capteur)
    X_aug = np.vstack([X_aug, feature_jitter(X_real, rng), feature_jitter(X_aug[: len(X_real)], rng)])
    X = np.vstack([X_real, X_aug])
    scaler = StandardScaler().fit(X)
    forest = IsolationForest(n_estimators=N_ESTIMATORS, max_samples=512, contamination=CONTAMINATION,
                             random_state=SEED, n_jobs=-1).fit(scaler.transform(X))
    s_train = -forest.score_samples(scaler.transform(X))

    med = np.median(X, axis=0)
    mad = np.median(np.abs(X - med), axis=0) * 1.4826
    scale = np.maximum(mad, np.maximum(0.25 * X.std(axis=0), 1e-6))
    # plancher resolution capteur (z-scores de label() et interpretabilite)
    scale = np.maximum(scale, np.array([SCALE_FLOOR[f] for f in FEATURES]))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"scaler": scaler, "forest": forest}, MODEL_DIR / "model.joblib")
    t0 = pd.to_datetime(train_segs[0]["created_at"].iloc[0], utc=True)
    t1 = pd.to_datetime(train_segs[-1]["created_at"].iloc[-1], utc=True)
    meta = {
        "model": "IsolationForest",
        "library": "scikit-learn",
        "device": device,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_period_utc": [t0.isoformat(), t1.isoformat()],
        "n_samples_real": int(len(X_real)),
        "n_samples_augmented": int(len(X_aug)),
        "n_samples": int(len(X)),
        "n_segments_train": len(train_segs),
        "excluded_periods": load_exclusions(),
        "contamination": CONTAMINATION,
        "n_estimators": N_ESTIMATORS,
        "random_state": SEED,
        "augmentation": {
            "gas_uniform_noise": AUG_GAS_NOISE,
            "temp_uniform_noise": AUG_TEMP_NOISE,
            "hum_uniform_noise": AUG_HUM_NOISE,
            "person_nearby": True,
            "feature_jitter_floors": SCALE_FLOOR,
        },
        "features": FEATURES,
        "sample_period_s": DT_S,
        "calibration": {
            "score_p50": float(np.quantile(s_train, 0.5)),
            "score_vigilance": float(np.quantile(s_train, 0.98)),
            "score_threshold": float(-forest.offset_),
            "score_extreme": 0.0,
        },
        "robust_stats": {f: {"median": float(m), "scale": float(sc)} for f, m, sc in zip(FEATURES, med, scale)},
    }
    (MODEL_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))
    # score d'un incident extreme de reference (spray butane x6) pour borner le risque a 100
    model = Model(MODEL_DIR)
    ref = train_segs[-1].iloc[: W_LONG + 40].reset_index(drop=True)
    ref, _ = inject_butane(ref, W_LONG + 20, 6 * float(ref["gas"].mean()), np.random.default_rng(0))
    s_ext = float(np.nanmax(stream(model, ref)["score"]))
    meta["calibration"]["score_extreme"] = max(s_ext, meta["calibration"]["score_threshold"] + 0.05)
    (MODEL_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))
    print(f"[train] {len(X_real)} echantillons reels + {len(X_aug)} augmentes, {len(train_segs)} sequences")
    print(f"[train] calibration {json.dumps(meta['calibration'])}")
    return train_segs, test_segs


# ---------------------------------------------------------------- evaluation
def evaluate(test_segs: list[pd.DataFrame], full: pd.DataFrame | None = None) -> str:
    model = Model(MODEL_DIR)
    m = model.meta
    rng = np.random.default_rng(SEED + 1)
    L: list[str] = []
    out = L.append
    out("# SENTINEL-X - Evaluation du modele de maintenance predictive\n")
    out(f"Genere le {datetime.now(timezone.utc).isoformat(timespec='seconds')} par `ml/train.py` (graine {SEED}).\n")
    out("## Modele\n")
    out(f"- Isolation Forest scikit-learn, {m['n_estimators']} arbres, contamination {m['contamination']}")
    out(f"- Entrainement : {m['n_samples_real']} fenetres reelles ({m['device']}, {m['train_period_utc'][0]} -> {m['train_period_utc'][1]} UTC) "
        f"+ {m['n_samples_augmented']} fenetres augmentees (bruit MQ-2 +-{m['augmentation']['gas_uniform_noise']:g} ADC, "
        f"DHT22 +-{m['augmentation'].get('temp_uniform_noise', 0.5):g} C / +-{m['augmentation'].get('hum_uniform_noise', 2):g} %RH, "
        f"scenarios 'personne proche')")
    out(f"- Features ({len(m['features'])}) : {', '.join(m['features'])} "
        "(projection directionnelle : seules hausses gaz/temp, chutes d'humidite, corr. positive)")
    c = m["calibration"]
    out(f"- Calibration du score (appris) : mediane {c['score_p50']:.3f}, vigilance q98 {c['score_vigilance']:.3f} (risque 30), "
        f"seuil anomalie {c['score_threshold']:.3f} (risque 50), incident extreme {c['score_extreme']:.3f} (risque 100)")
    out("- Decision : 4 fenetres anormales consecutives (8 s) ; fin d'incident apres 5 fenetres normales\n")
    out("\n### Features directionnelles et jeu d'entrainement\n")
    out("L'Isolation Forest etant symetrique, `vectorize()` ne laisse passer que le sens du danger "
        "(hausse de gaz, echauffement, chute d'humidite, correlation gaz-temperature positive) ; "
        "les baisses et retours a la normale sont mis a zero (neutre). "
        "Le jeu d'entrainement inclut des periodes calmes du matin et de l'apres-midi "
        "(apres rebranchement MQ-2 et stabilisation), avec exclusion des essais reels "
        "(spray briquet, chauffage DHT22) et des rechauffes post-reboot — voir `training_exclusions.json`.\n")

    # 1. donnees normales de test (20 % les plus recentes, jamais vues a l'entrainement)
    out("## 1. Donnees normales (holdout temporel)\n")
    hours, n_s, n_anom, fp_inc, fp_pred, risks = 0.0, 0, 0, 0, 0, []
    hours_n, fp_inc_n, n_anom_n = 0.0, 0, 0
    for s in test_segs:
        r = stream(model, s)
        v = r[r.state != "warmup"]
        n_s += len(v); n_anom += int(v.anom.sum()); risks += list(v.risk)
        hours += len(v) * DT_S / 3600
        fp_inc += int(((v.state.isin(["gaz_fumee", "thermique", "anomalie"])) &
                       ~(v.state.shift().isin(["gaz_fumee", "thermique", "anomalie"]))).sum())
        fp_pred += int(((v.state == "prediction") & (v.state.shift() != "prediction")).sum())
        rn = stream(model, jitter(s, rng))
        vn = rn[rn.state != "warmup"]
        hours_n += len(vn) * DT_S / 3600; n_anom_n += int(vn.anom.sum())
        fp_inc_n += int(((vn.state.isin(["gaz_fumee", "thermique", "anomalie"])) &
                         ~(vn.state.shift().isin(["gaz_fumee", "thermique", "anomalie"]))).sum())
    risks = np.array(risks) if risks else np.zeros(1)
    out("| Jeu | Fenetres | Duree | Fenetres > seuil | Incidents (faux positifs) | Episodes 'prediction' | Risque median / p99 |")
    out("|---|---|---|---|---|---|---|")
    out(f"| Normal reel | {n_s} | {hours:.2f} h | {n_anom} ({100*n_anom/max(n_s,1):.2f} %) | {fp_inc} | {fp_pred} | {np.median(risks):.1f} / {np.quantile(risks,0.99):.1f} |")
    out(f"| Normal + bruit gaz +-{AUG_GAS_NOISE:g} | {int(hours_n*1800)} | {hours_n:.2f} h | {n_anom_n} | {fp_inc_n} | - | - |\n")

    # 2. incidents synthetiques injectes dans les donnees de test
    base_seg = max(test_segs, key=len)
    long_base = tile(base_seg, W_LONG + 150 + 2000)
    scen = []
    n_inj = 20
    for k in range(n_inj):
        L0 = W_LONG + 150
        st = int(rng.integers(0, len(long_base) - L0 - 200))
        seg = long_base.iloc[st:st + L0 + 200].reset_index(drop=True)
        at = L0
        scen.append(("butane", k, seg, at, rng.uniform(200, 600)))
    rows = []
    tp = 0
    lat = []
    for name, k, seg, at, peak in scen:
        s2, _ = inject_butane(seg, at, peak, rng)
        r = stream(model, s2)
        pre_fp = r[(r.i < at) & r.state.isin(["gaz_fumee", "thermique", "anomalie"])]
        d = first(r, r.state == "gaz_fumee", at)
        # PERSIST_N=4 => ~8 s ; on tolere 12 s. Un FP avant injection (holdout bruyant) ne
        # disqualifie pas la detection du spray lui-meme.
        ok = d is not None and (d - at) * DT_S <= 12
        tp += ok
        if d is not None:
            lat.append((d - at) * DT_S)
    out("## 2. Spray de gaz butane simule (cas de la demo)\n")
    out(f"{n_inj} injections : gaz de ~80 a 200-600 ADC en 2-4 s, plateau 10-30 s, decroissance ~60 s.\n")
    out(f"- Detectes en `gaz_fumee` en <= 12 s : **{tp}/{n_inj}** (rappel {100*tp/n_inj:.0f} %)")
    if lat:
        out(f"- Latence de declenchement (debut du spray -> alarme) : mediane {np.median(lat):.0f} s, max {max(lat):.0f} s\n")

    # 2b. pics isoles (glitch ADC / parasite) : ne doivent PAS declencher (persistance + mediane)
    n_gl, fp_gl = 20, 0
    for k in range(n_gl):
        L0 = W_LONG + 150
        st = int(rng.integers(0, len(long_base) - L0 - 60))
        seg = long_base.iloc[st:st + L0 + 60].reset_index(drop=True)
        seg["gas"] = seg["gas"].astype(float)
        seg.loc[L0, "gas"] = rng.uniform(300, 1024)
        r = stream(model, seg)
        fp_gl += int(r[r.i >= L0].state.isin(["gaz_fumee", "thermique", "anomalie"]).any())
    out(f"- Pics isoles d'un seul echantillon (300-1024 ADC, glitch) : **{fp_gl}/{n_gl}** declenchements (attendu 0)\n")

    # 3. fuite lente + echauffement lent : avance sur une regle statique
    out("## 3. Detection precoce (derives lentes) vs regle statique\n")
    out("Comparaison avec ce que ferait une regle statique de type `if gaz > 300` ou `if temp > 40` : "
        "on mesure l'avance de l'IA (premiere `prediction` et incident confirme).\n")
    out("| Scenario | Prediction a | Incident a (type) | Regle statique atteinte a | Avance IA |")
    out("|---|---|---|---|---|")
    slow_ok = 0
    slow_n = 0
    for name, kind, a, b in [
        ("Fuite lente +20 ADC/min", "leak", 20, 0), ("Fuite lente +8 ADC/min", "leak", 8, 0),
        ("Echauffement +0.5 degC/min + gaz +2 ADC/min", "heat", 0.5, 2),
        ("Echauffement +0.3 degC/min + gaz +1 ADC/min", "heat", 0.3, 1),
    ]:
        L0 = W_LONG + 150
        need = L0 + 1800
        seg = long_base.iloc[:need].reset_index(drop=True)
        if kind == "leak":
            s2 = inject_leak_ramp(seg, L0, a)
            g0 = float(seg["gas"].iloc[L0 - 30:L0].mean())
            crit = L0 + int(np.ceil((300 - g0) / (a * DT_S / 60)))
            crit_lbl = "gaz > 300"
        else:
            s2 = inject_heat(seg, L0, a, b)
            t0 = float(seg["temperature"].iloc[L0 - 30:L0].mean())
            crit = L0 + int(np.ceil((40 - t0) / (a * DT_S / 60)))
            crit_lbl = "temp > 40 degC"
        r = stream(model, s2)
        p = first(r, r.state == "prediction", L0)
        inc = first(r, r.state.isin(["gaz_fumee", "thermique", "anomalie"]), L0)
        lab = r.loc[r.i == inc, "state"].iloc[0] if inc is not None else "-"
        fmt = lambda i: f"{(i - L0) * DT_S / 60:.1f} min" if i is not None else "non"
        det = min([v for v in (p, inc) if v is not None], default=None)
        adv = f"{(crit - det) * DT_S / 60:.1f} min" if det is not None else "-"
        slow_n += 1
        slow_ok += det is not None and det < crit
        out(f"| {name} | {fmt(p)} | {fmt(inc)} ({lab}) | {(crit - L0) * DT_S / 60:.1f} min ({crit_lbl}) | {adv} |")
    out("")

    # 4. rejeu de l'historique reel complet (y compris les pics de gaz reels exclus de l'entrainement)
    if full is not None and len(full):
        ex = load_exclusions()
        t = pd.to_datetime(full["created_at"], utc=True)
        lo = pd.Timestamp(ex[0]["end"]) if ex else t.min()
        hi = pd.Timestamp(ex[-1]["start"]) if ex else t.max()
        real = full[(t >= lo) & (t < hi)].reset_index(drop=True)
        if len(real) > W_LONG:
            r = stream(model, real)
            tt = pd.to_datetime(real["created_at"], utc=True).dt.tz_convert("Europe/Paris")
            inc = r[r.state.isin(["gaz_fumee", "thermique", "anomalie"]) & (r.state != r.state.shift())]
            pred = r[(r.state == "prediction") & (r.state.shift() != "prediction")]
            out("## 4. Rejeu de l'historique reel (pics de gaz reellement enregistres)\n")
            out(f"Rejeu en flux de {len(real)} mesures reelles ({tt.iloc[0]:%H:%M} -> {tt.iloc[-1]:%H:%M}, heure de Paris), "
                "incluant les pics de gaz exclus de l'entrainement.\n")
            out("| Debut incident / escalade (Paris) | Type | Gaz brut | Risque |")
            out("|---|---|---|---|")
            for _, row in inc.iterrows():
                i = int(row.i)
                out(f"| {tt.iloc[i]:%H:%M:%S} | {row.state} | {int(real['gas'].iloc[i])} | {row.risk:.0f} |")
            if inc.empty:
                out("| - | aucun | - | - |")
            out(f"\nEpisodes `prediction` (pre-alerte sans son) : {len(pred)}.\n")

    fp_total = fp_inc
    prec = tp / max(tp + fp_total, 1)
    out("## Synthese\n")
    out(f"- Rappel spray butane (<= 12 s) : {100*tp/n_inj:.0f} %")
    out(f"- Faux incidents sur {hours:.2f} h de donnees normales : {fp_inc} ({fp_inc/max(hours,1e-9):.2f} / h)")
    out(f"- Precision approchee (incidents vrais / incidents leves) : {100*prec:.0f} %")
    out(f"- Derives lentes detectees avant la regle statique : {slow_ok}/{slow_n}")
    out(f"- Pics isoles (glitch) ignores : {n_gl - fp_gl}/{n_gl}")
    txt = "\n".join(L) + "\n"
    EVAL_FILE.write_text(txt)
    return txt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--eval-only", action="store_true")
    a = ap.parse_args()
    device = os.getenv("ML_TRAIN_DEVICE", "sentinel-node-01")
    df = pd.read_csv(a.csv) if a.csv else load_db(device)
    print(f"[data] {len(df)} lignes de telemetrie ({device})")
    if a.eval_only:
        segs = segments(apply_exclusions(df, load_exclusions()))
        test = segs[-max(1, len(segs) // 5):]
    else:
        _, test = train(df, device)
    print(evaluate(test, df))


if __name__ == "__main__":
    main()
