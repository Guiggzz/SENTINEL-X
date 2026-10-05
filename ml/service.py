"""SENTINEL-X - service temps reel de maintenance predictive (conteneur sentinel-ml).

Abonne a la telemetrie MQTT, calcule les features cinetiques, score Isolation Forest,
publie le risque 0-100 sur sentinel/<device>/ai et pilote l'alarme gaz/fumee.

Variables d'environnement (memes noms que l'API) :
  MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_TLS (true/false), MQTT_CA_FILE,
  MQTT_TELEMETRY_TOPIC (defaut sentinel/+/telemetry), DATABASE_URL (ou POSTGRES_*)
Topics : publie sentinel/ai/<device>/risk (risque), sentinel/ai/alerts, sentinel/ai/status (LWT),
  sentinel/<actionneur>/cmd ; lit sentinel/+/telemetry (le champ buzzer sert d'acquittement).
Specifiques : ML_MODEL_DIR, ML_ACTUATOR_DEVICE, ML_GAS_BUZZER_MS (0 = boucle jusqu'a arret),
  ML_REARM_S, ML_CLIENT_ID
"""
from __future__ import annotations

import json
import logging
import os
import signal
import ssl
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import paho.mqtt.client as mqtt

from detector import IncidentTracker, Model
from features import FeatureState, vectorize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sentinel.ml")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


MQTT_HOST = os.getenv("MQTT_HOST", "sentinel-mosquitto")
MQTT_TLS = env_bool("MQTT_TLS")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883" if MQTT_TLS else "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None
MQTT_CA_FILE = os.getenv("MQTT_CA_FILE") or None
ML_STEP_S = float(os.getenv("ML_STEP_S", "1.9"))  # pas d'analyse IA (s)
TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "sentinel/+/telemetry")
MODEL_DIR = os.getenv("ML_MODEL_DIR", "/app/models")
ACTUATOR = os.getenv("ML_ACTUATOR_DEVICE", "sentinel-node-01")
GAS_BUZZER_MS = int(os.getenv("ML_GAS_BUZZER_MS", "0"))
REARM_S = float(os.getenv("ML_REARM_S", "60"))
CLIENT_ID = os.getenv("ML_CLIENT_ID", "sentinel-ml")
NO_STATUS = os.getenv("ML_NO_STATUS") == "1"      # instance de test : pas de LWT / statut
ML_ID = "sentinel-ml"
# topics alignes sur l'ACL Mosquitto (utilisateur sentinel-ml : write sentinel/ai/#, sentinel/+/cmd)
AI_TOPIC_FMT = os.getenv("ML_AI_TOPIC", "sentinel/ai/{device}/risk")
T_ALERTS = os.getenv("ML_ALERTS_TOPIC", "sentinel/ai/alerts")
T_STATUS = os.getenv("ML_STATUS_TOPIC", "sentinel/ai/status")
T_CMD = f"sentinel/{ACTUATOR}/cmd"
INCIDENTS = ("gaz_fumee", "thermique", "anomalie")
RESET_GAP_S = 600


@dataclass
class DeviceCtx:
    fs: FeatureState = field(default_factory=FeatureState)
    tr: IncidentTracker = field(default_factory=IncidentTracker)
    last_rx: float = 0.0
    last_pred_alert: float = 0.0
    last_uptime: int | None = None


class Service:
    def __init__(self) -> None:
        self.model = Model(MODEL_DIR)
        m = self.model.meta
        self.model_info = {"type": m["model"], "n_samples": m["n_samples"], "n_real": m["n_samples_real"],
                           "trained_at": m["trained_at"], "contamination": m["contamination"],
                           "n_features": len(m["features"])}
        self.devices: dict[str, DeviceCtx] = {}
        self.lock = threading.Lock()
        # etat de la sirene gaz (partagee : un seul actionneur)
        self.siren_on = False
        self.siren_owner: str | None = None
        self.sources = {"pir": True, "vision": True, "gaz_ia": True, "thermique_ia": True}
        self.last_stop = 0.0
        self.silenced = False
        self.siren_cmd_at = 0.0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID, protocol=mqtt.MQTTv311)
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        if MQTT_TLS:
            self.client.tls_set(ca_certs=MQTT_CA_FILE, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        if not NO_STATUS:
            self.client.will_set(T_STATUS, "offline", qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = lambda *a, **k: log.warning("MQTT deconnecte")
        self.client.reconnect_delay_set(1, 30)

    # ------------------------------------------------------------ MQTT
    def run(self) -> None:
        self.preload()
        log.info("MQTT -> %s:%s tls=%s user=%s", MQTT_HOST, MQTT_PORT, MQTT_TLS, bool(MQTT_USERNAME))
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.client.loop_start()
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        stop.wait()
        self.client.publish(T_STATUS, "offline", qos=1, retain=True)
        self.client.loop_stop()

    def on_connect(self, client, userdata, flags, rc, props=None):
        if rc != 0 and str(rc) not in ("Success", "0"):
            log.error("Connexion MQTT refusee : %s", rc)
            return
        if NO_STATUS:
            return
        client.subscribe([(TELEMETRY_TOPIC, 0), ("sentinel/config/sources", 1)])
        client.publish(T_STATUS, "online", qos=1, retain=True)
        log.info("MQTT connecte, abonne a %s + sources", TELEMETRY_TOPIC)

    def pub(self, topic: str, payload: dict, qos: int = 0) -> None:
        self.client.publish(topic, json.dumps(payload), qos=qos)

    def cmd(self, payload: dict) -> None:
        self.pub(T_CMD, payload, qos=1)
        log.info("CMD %s %s", T_CMD, payload)

    def alert(self, type_: str, state: str, extra: dict) -> None:
        body = {"device_id": ML_ID, "type": type_, "state": state, "uptime_ms": None, **extra}
        self.pub(T_ALERTS, body, qos=1)
        log.info("ALERTE %s/%s %s", type_, state, extra.get("source_device"))

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        try:
            with self.lock:
                if msg.topic == "sentinel/config/sources":
                    for k in ("pir", "vision", "gaz_ia", "thermique_ia"):
                        if k in data:
                            self.sources[k] = bool(data[k])
                    log.info("Sources alarmes maj : %s", self.sources)
                    return
                if msg.topic.endswith("/telemetry"):
                    dev = str(data.get("device_id") or msg.topic.split("/")[1])
                    if dev == ACTUATOR:
                        self.on_actuator(data)
                    self.on_telemetry(dev, data)
        except Exception:
            log.exception("Erreur de traitement %s", msg.topic)

    # ------------------------------------------------------------ logique
    def preload(self) -> None:
        """Rechauffe la ligne de base avec les dernieres minutes stockees en base (evite 2 min de chauffe)."""
        try:
            import psycopg
            from train import db_url
            with psycopg.connect(db_url(), connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute("SELECT gas, temperature, humidity, uptime_ms FROM (SELECT gas, temperature, humidity, "
                            "uptime_ms, created_at FROM telemetry WHERE device_id=%s AND created_at > now() - interval "
                            "'10 minutes' ORDER BY created_at DESC LIMIT 1200) t ORDER BY created_at", (ACTUATOR,))
                rows = cur.fetchall()
            # ne garder que la session courante de l'ESP (apres le dernier redemarrage)
            start = 0
            for i in range(1, len(rows)):
                if rows[i][3] is not None and rows[i - 1][3] is not None and rows[i][3] < rows[i - 1][3]:
                    start = i
            rows = rows[start:]
            ctx = self.devices.setdefault(ACTUATOR, DeviceCtx())
            last = None
            for g, t, h, up in rows:
                if up is not None and last is not None and 0 <= up - last < ML_STEP_S * 1000:
                    continue                      # meme sous-echantillonnage 2 s qu'en direct
                if up is not None:
                    last = up
                ctx.fs.update(g, t, h)
                ctx.last_uptime = up
            ctx.last_ml_t = last / 1000.0 if last is not None else None
            ctx.last_rx = time.time()
            ctx.tr.state = "normal" if ctx.fs.n >= 60 else "warmup"
            log.info("Prechauffage %s : %d mesures", ACTUATOR, len(rows))
        except Exception as exc:
            log.warning("Prechauffage impossible (%s), chauffe en direct (2 min)", exc)

    def on_actuator(self, data: dict) -> None:
        # etat reel du buzzer remonte par la telemetrie de l'actionneur : si l'operateur a coupe l'alarme
        # ("Couper alarme" -> buzzer_off), la sirene n'est plus active -> silence + rearmement differe
        if self.siren_on and time.time() - self.siren_cmd_at > 6 and data.get("buzzer") is False:
            self.siren_on = False
            self.silenced = True
            self.last_stop = time.time()
            log.info("Sirene coupee par l'operateur, rearmement dans %.0f s", REARM_S)

    def on_telemetry(self, dev: str, data: dict) -> None:
        now = time.time()
        ctx = self.devices.get(dev)
        up = data.get("uptime_ms")
        rebooted = ctx is not None and isinstance(up, int) and ctx.last_uptime is not None and up < ctx.last_uptime
        if ctx is None or now - ctx.last_rx > RESET_GAP_S or rebooted:
            if rebooted:
                log.info("%s a redemarre (uptime %s ms) : nouvelle ligne de base (chauffe MQ-2)", dev, up)
                if ctx.tr.state in INCIDENTS:
                    self.cmd({"action": "led_auto"})
            ctx = self.devices[dev] = DeviceCtx()
        ctx.last_rx = now
        if isinstance(up, int):
            ctx.last_uptime = up
        # L'ESP publie toutes les 0,5 s (fluidite du dashboard) ; le modele a ete entraine sur un pas de 2 s
        # et ses fenetres sont en nombre d'echantillons -> on sous-echantillonne a ~2 s pour l'IA.
        t_ref = up / 1000.0 if isinstance(up, int) else now
        last_ml = getattr(ctx, "last_ml_t", None)
        if last_ml is not None and 0 <= t_ref - last_ml < ML_STEP_S:
            return
        ctx.last_ml_t = t_ref
        freeze = ctx.tr.state not in ("normal", "warmup")
        f = ctx.fs.update(data.get("gas"), data.get("temperature"), data.get("humidity"), freeze=freeze)
        base = {"device_id": dev, "ts": now, "model": self.model_info}
        if f is None:
            self.pub(AI_TOPIC_FMT.format(device=dev), {**base, "state": "warmup", "risk": None,
                                            "warmup_pct": round(100 * ctx.fs.n / 60)})
            return
        x = vectorize(f)
        t0 = time.perf_counter()
        s = float(self.model.score(x)[0])
        infer_ms = (time.perf_counter() - t0) * 1000
        risk = float(self.model.risk(s))
        anom = s > self.model.s_thr
        lab, top = self.model.label(x)
        prev = ctx.tr.state
        state = ctx.tr.step(risk, anom, lab)
        extra = {"source_device": dev, "risk": round(risk, 1), "score": round(s, 4), "top_features": top}

        if ctx.tr.incident_started:
            if state == "gaz_fumee":
                self.alert("ia_gaz", "gaz_fumee_detecte", extra)
            elif state == "thermique":
                self.alert("ia_thermique", "derive_thermique_detectee", extra)
            else:
                self.alert("ia_anomalie", "anomalie_cinetique", extra)
            if state == "gaz_fumee" and self.sources.get("gaz_ia", True):
                self.cmd({"action": "led", "color": "red", "state": True})
                self.cmd({"action": "led", "color": "green", "state": False})
            elif state == "thermique" and self.sources.get("thermique_ia", True):
                self.cmd({"action": "led", "color": "red", "state": True})
                self.cmd({"action": "led", "color": "green", "state": False})
        elif state == "prediction" and prev != "prediction" and now - ctx.last_pred_alert > 60:
            ctx.last_pred_alert = now
            self.alert("ia_prediction", "risque_croissant", extra)

        # sirene gaz : sonne tant que l'incident dure, independamment de l'alarme presence
        if (state == "gaz_fumee" and self.sources.get("gaz_ia", True)
                and not self.siren_on and now - self.last_stop >= REARM_S):
            self.cmd({"action": "buzzer_on", "duration_ms": GAS_BUZZER_MS, "song": "gaz"})
            self.siren_on, self.siren_owner, self.silenced = True, dev, False
            self.siren_cmd_at = now

        if ctx.tr.incident_cleared:
            self.alert({"gaz_fumee": "ia_gaz", "thermique": "ia_thermique"}.get(prev, "ia_anomalie"),
                       {"gaz_fumee": "gaz_fumee_termine", "thermique": "derive_thermique_terminee"}.get(prev, "anomalie_terminee"),
                       extra)
            if self.siren_on and self.siren_owner == dev:
                self.cmd({"action": "buzzer_off"})
                self.siren_on = False
                self.last_stop = now
            if prev in ("gaz_fumee", "thermique") and not any(c.tr.state in ("gaz_fumee", "thermique") for c in self.devices.values()):
                self.cmd({"action": "led_auto"})
                self.silenced = False

        self.pub(AI_TOPIC_FMT.format(device=dev), {
            **base, "state": state, "risk": round(risk, 1), "risk_smooth": round(ctx.tr.risk_smooth, 1),
            "score": round(s, 4), "anomalous": bool(anom), "candidate": lab if anom else "normal",
            "consecutive": ctx.tr.consec_anom, "top_features": top,
            "alarm": self.siren_on and self.siren_owner == dev, "silenced": self.silenced and state == "gaz_fumee",
            "infer_ms": round(infer_ms, 2),
            "features": {k: round(float(v), 4) for k, v in f.items()},
        })


if __name__ == "__main__":
    Service().run()
