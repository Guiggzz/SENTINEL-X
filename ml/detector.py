"""SENTINEL-X - modele de maintenance predictive (Isolation Forest) + machine a etats.

- score d'anomalie = -IsolationForest.score_samples() sur features standardisees
- seuils de decision APPRIS sur l'historique normal (quantiles du score), pas de seuil capteur
- risque 0-100 : interpolation par morceaux du score entre mediane normale, vigilance (q98),
  seuil d'anomalie (offset IF / contamination) et score d'un incident extreme de reference
- type d'incident : contribution des features (z-scores robustes median/MAD appris) ->
  le groupe de features dominant (gaz vs thermique) nomme l'incident
- persistance : N fenetres anormales consecutives avant de lever l'incident
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np

from features import FEATURES

PERSIST_N = 4        # fenetres anormales consecutives pour lever un incident (~8 s a DT=2 s)
CLEAR_N = 5          # fenetres normales consecutives pour lever la fin d'incident (~10 s)
RELABEL_N = 5        # fenetres consecutives ou le groupe gaz/thermique ne domine plus -> fin de l'incident type
DOMINANCE = 2.0      # un groupe de features doit peser 2x l'autre pour nommer l'incident


class Model:
    def __init__(self, model_dir: str | Path):
        d = Path(model_dir)
        bundle = joblib.load(d / "model.joblib")
        self.scaler = bundle["scaler"]
        self.forest = bundle["forest"]
        self.meta = json.loads((d / "metadata.json").read_text())
        cal = self.meta["calibration"]
        self.s_p50 = cal["score_p50"]
        self.s_vig = cal["score_vigilance"]
        self.s_thr = cal["score_threshold"]
        self.s_max = cal["score_extreme"]
        self.med = np.array([self.meta["robust_stats"][f]["median"] for f in FEATURES])
        self.scale = np.array([self.meta["robust_stats"][f]["scale"] for f in FEATURES])

    def score(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        return -self.forest.score_samples(self.scaler.transform(X))

    def risk(self, s: np.ndarray | float) -> np.ndarray:
        s = np.asarray(s, dtype=float)
        xp = [self.s_p50, self.s_vig, self.s_thr, self.s_max]
        fp = [0.0, 30.0, 50.0, 100.0]
        return np.interp(s, xp, fp)

    def zscores(self, X: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(X) - self.med) / self.scale

    def label(self, x: np.ndarray) -> tuple[str, list[dict]]:
        """Nomme l'anomalie selon le groupe de features qui domine (z-scores robustes).

        Les features arrivees via vectorize() sont deja projetees sur le sens du danger
        (hausses gaz/temp, chute humidite). On ne recompte donc plus les baisses comme
        signature 'anomalie' : cela evitait les fausses alarmes au retour a la normale.
        """
        z = self.zscores(x)[0]
        zf = dict(zip(FEATURES, z))
        up = lambda k: max(zf[k], 0.0)          # deviations au-dessus de la mediane apprise
        gas_rise = max(up("gas_dev_rel"), up("gas_slope_30s"), up("gas_slope_2m"))
        # turbulence (std) : ne nomme gaz que si une hausse de gaz est deja presente
        g = max(gas_rise, up("gas_std_30s") if gas_rise > 0 else 0.0)
        # signature thermique : echauffement + assechement (hum_slope <= 0 apres vectorize)
        t = max(up("temp_slope_2m"), up("temp_dev"), max(-zf["hum_slope_2m"], 0.0), up("gas_temp_corr"))
        # un groupe doit peser DOMINANCE x l'autre ; sinon anomalie non typee
        if g > 0 and g >= DOMINANCE * t:
            lab = "gaz_fumee"
        elif t > 0 and t >= DOMINANCE * g:
            lab = "thermique"
        else:
            lab = "anomalie"
        order = np.argsort(-np.abs(z))[:3]
        top = [{"feature": FEATURES[i], "z": round(float(z[i]), 2)} for i in order]
        return lab, top


@dataclass
class IncidentTracker:
    """Machine a etats : normal -> prediction -> incident (gaz_fumee/thermique/anomalie) -> normal."""

    state: str = "warmup"
    consec_anom: int = 0
    consec_norm: int = 0
    consec_offlabel: int = 0
    risk_hist: deque = field(default_factory=lambda: deque(maxlen=15))
    risk_smooth: float = 0.0
    incident_started: bool = False   # True uniquement sur la transition vers incident
    incident_cleared: bool = False

    def step(self, risk: float, anomalous: bool, label: str) -> str:
        """label = groupe de features dominant (calcule a chaque fenetre, meme non anormale)."""
        self.incident_started = self.incident_cleared = False
        hazard = label in ("gaz_fumee", "thermique")
        self.risk_smooth = 0.6 * self.risk_smooth + 0.4 * risk
        self.risk_hist.append(self.risk_smooth)
        if anomalous:
            self.consec_anom += 1
            self.consec_norm = 0
        else:
            self.consec_norm += 1
            self.consec_anom = 0

        in_incident = self.state in ("gaz_fumee", "thermique", "anomalie")
        if in_incident:
            # gaz_fumee/thermique : si la signature n'est plus celle du danger (ex. gaz revenu a sa base,
            # seule la temperature redescend), l'incident type se termine ; un ecart residuel devra
            # se re-confirmer (PERSIST_N) et sera alors nomme selon sa nouvelle signature.
            if self.state in ("gaz_fumee", "thermique") and label != self.state:
                self.consec_offlabel += 1
            else:
                self.consec_offlabel = 0
            if self.consec_norm >= CLEAR_N or self.consec_offlabel >= RELABEL_N:
                self.state = "normal"
                self.incident_cleared = True
                self.consec_anom = 0
                self.consec_offlabel = 0
            elif anomalous and label != self.state and label == "gaz_fumee":
                self.state = label          # escalade vers gaz si le panache arrive
                self.incident_started = True
            return self.state

        if self.consec_anom >= PERSIST_N:
            self.state = label
            self.incident_started = True
            return self.state

        # alerte precoce : risque lisse en zone de vigilance ET tendance haussiere,
        # ou premieres fenetres anormales pas encore confirmees
        trend = 0.0
        if len(self.risk_hist) >= 5:
            y = np.array(self.risk_hist)
            x = np.arange(len(y)) - (len(y) - 1) / 2
            trend = float(np.dot(x, y - y.mean()) / np.dot(x, x))
        # pre-alerte seulement si la deviation va dans le sens d'un danger (hausse gaz / echauffement)
        rising = self.risk_smooth >= 30.0 and trend > 0 and hazard
        if rising or (self.consec_anom > 0 and hazard):
            self.state = "prediction"
        elif self.state == "prediction" and self.risk_smooth >= 25.0 and hazard:
            pass                            # hysteresis
        else:
            self.state = "normal"
        return self.state
