"""SENTINEL-X - extraction de features cinetiques (partagee entrainement / inference).

Le noeud ESP8266 publie une mesure toutes les 2 s (cadence fixe). Les fenetres
sont donc exprimees en nombre d'echantillons :
  - fenetre courte  15 echantillons = 30 s
  - fenetre longue  60 echantillons = 2 min
Aucune regle "si capteur > seuil" ici : on calcule uniquement des grandeurs
dynamiques (ecart a une ligne de base EWMA, pentes, dispersion, correlation)
qui alimentent le modele Isolation Forest.

Features directionnelles (vectorize) : seules les directions dangereuses
passent au modele (hausse de gaz, echauffement, chute d'humidite, correlation
gaz-temperature positive). Les baisses / retours a la normale sont mises a 0
(neutre) pour que la foret, symetrique par nature, ne traite pas une
decroissance benigne comme une anomalie.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

DT_S = 2.0            # periode nominale d'echantillonnage (s)
W_SHORT = 15          # 30 s
W_LONG = 60           # 2 min
GAS_BASE_N = 150      # ligne de base gaz EWMA ~ 5 min
TEMP_BASE_N = 300     # ligne de base temperature EWMA ~ 10 min
FREEZE_FACTOR = 0.05  # en anomalie la ligne de base n'apprend presque plus (evite d'absorber la fuite)
MED_N = 3             # filtre median 3 echantillons (6 s) : un pic isole (glitch ADC) ne compte pas

FEATURES = [
    "gas_dev_rel",     # (gaz - base EWMA) / base           -> micro-deviation / fuite (hausse seule)
    "gas_slope_30s",   # pente gaz 30 s, relative, /min     -> montee rapide (spray butane)
    "gas_std_30s",     # dispersion gaz 30 s (MAD), relative -> turbulence du panache (si gaz monte)
    "gas_slope_2m",    # pente gaz 2 min, relative, /min    -> derive lente (hausse seule)
    "temp_slope_2m",   # pente temperature 2 min, degC/min  -> echauffement (hausse seule)
    "temp_dev",        # temperature - base EWMA 10 min, degC (hausse seule)
    "hum_slope_2m",    # pente humidite 2 min, %/min (chute seule = assechement)
    "gas_temp_corr",   # correlation de Pearson gaz x temperature sur 2 min (positive seule)
]
GAS_FEATURES = ["gas_dev_rel", "gas_slope_30s", "gas_std_30s", "gas_slope_2m"]
THERMAL_FEATURES = ["temp_slope_2m", "temp_dev", "hum_slope_2m"]


def _slope(y: np.ndarray) -> float:
    """Pente par moindres carres, unites / seconde."""
    n = len(y)
    if n < 3:
        return 0.0
    x = np.arange(n, dtype=float) * DT_S
    x -= x.mean()
    return float(np.dot(x, y - y.mean()) / np.dot(x, x))


def _median_filter(y: np.ndarray) -> np.ndarray:
    """Mediane glissante causale sur MED_N echantillons."""
    if len(y) < MED_N:
        return y.copy()
    out = y.copy()
    w = np.lib.stride_tricks.sliding_window_view(y, MED_N)
    out[MED_N - 1:] = np.median(w, axis=1)
    return out


def _corr(a: np.ndarray, b: np.ndarray, min_std_a: float, min_std_b: float) -> float:
    if len(a) < 10:
        return 0.0
    sa, sb = a.std(), b.std()
    if sa < min_std_a or sb < min_std_b:
        return 0.0          # signal plat (quantification capteur) : pas de correlation exploitable
    return float(np.clip(np.corrcoef(a, b)[0, 1], -1.0, 1.0))


@dataclass
class FeatureState:
    """Etat glissant par equipement. update() renvoie le vecteur de features (ou None en chauffe)."""

    gas: deque = field(default_factory=lambda: deque(maxlen=W_LONG))
    temp: deque = field(default_factory=lambda: deque(maxlen=W_LONG))
    hum: deque = field(default_factory=lambda: deque(maxlen=W_LONG))
    gas_base: float | None = None
    temp_base: float | None = None
    n: int = 0
    last_t: float | None = None
    last_h: float | None = None

    def update(self, gas: float | None, temp: float | None, hum: float | None,
               freeze: bool = False) -> dict[str, float] | None:
        if gas is None:
            return None
        # capteur DHT22 : valeurs manquantes -> on prolonge la derniere valeur connue
        if temp is None:
            temp = self.last_t
        if hum is None:
            hum = self.last_h
        if temp is None or hum is None:
            return None
        self.last_t, self.last_h = temp, hum
        gas = float(gas)

        if self.gas_base is None:
            self.gas_base, self.temp_base = gas, float(temp)

        self.gas.append(gas)
        self.temp.append(float(temp))
        self.hum.append(float(hum))
        self.n += 1

        base = max(self.gas_base, 20.0)
        g_raw = np.fromiter(self.gas, float)
        g = _median_filter(g_raw)                 # serie gaz debarrassee des pics isoles
        # DHT22 : un releve aberrant isole (ex. 11.5 degC / 27 % entre deux 23 degC) ne doit pas fausser les pentes
        t = _median_filter(np.fromiter(self.temp, float))
        h = _median_filter(np.fromiter(self.hum, float))
        gs = g[-W_SHORT:]
        gs_raw = g_raw[-W_SHORT:]
        mad = float(np.median(np.abs(gs_raw - np.median(gs_raw)))) * 1.4826
        feats = {
            "gas_dev_rel": (g[-1] - self.gas_base) / base,
            "gas_slope_30s": _slope(gs) * 60.0 / base,
            "gas_std_30s": mad / base,             # dispersion robuste (MAD) : insensible a 1 glitch
            "gas_slope_2m": _slope(g) * 60.0 / base,
            "temp_slope_2m": _slope(t) * 60.0,
            "temp_dev": float(t[-1]) - self.temp_base,
            "hum_slope_2m": _slope(h) * 60.0,
            "gas_temp_corr": _corr(g, t, 1.0, 0.05),
        }

        # mise a jour des lignes de base APRES calcul (l'echantillon courant est compare au passe)
        k = FREEZE_FACTOR if freeze else 1.0
        a_g = k * 2.0 / (GAS_BASE_N + 1)
        a_t = k * 2.0 / (TEMP_BASE_N + 1)
        self.gas_base += a_g * (gas - self.gas_base)
        self.temp_base += a_t * (float(temp) - self.temp_base)

        if self.n < W_LONG:          # chauffe : fenetre longue pas encore pleine
            return None
        return feats


def vectorize(feats: dict[str, float]) -> np.ndarray:
    """Projette les features brutes vers le sens du danger (entrainement = inference).

    L'Isolation Forest est symetrique : sans cette projection, une baisse de gaz
    apres un essai (retour a la normale) ou un refroidissement scoreraient comme
    une montee. On annule donc les directions benignes :
      - gaz / temperature / correlation : seules les hausses (max(., 0))
      - humidite : seule la chute (assechement feu/chauffage) ; une hausse
        (respiration, personne proche) est neutre
      - turbulence gaz (std) : uniquement si le gaz monte (sinon bruit de retour)
    """
    gas_rising = (
        feats["gas_dev_rel"] > 0.0
        or feats["gas_slope_30s"] > 0.0
        or feats["gas_slope_2m"] > 0.0
    )
    directed = {
        "gas_dev_rel": max(feats["gas_dev_rel"], 0.0),
        "gas_slope_30s": max(feats["gas_slope_30s"], 0.0),
        "gas_std_30s": feats["gas_std_30s"] if gas_rising else 0.0,
        "gas_slope_2m": max(feats["gas_slope_2m"], 0.0),
        "temp_slope_2m": max(feats["temp_slope_2m"], 0.0),
        "temp_dev": max(feats["temp_dev"], 0.0),
        "hum_slope_2m": min(feats["hum_slope_2m"], 0.0),   # chute seule
        "gas_temp_corr": max(feats["gas_temp_corr"], 0.0),
    }
    return np.array([directed[f] for f in FEATURES], dtype=float)
