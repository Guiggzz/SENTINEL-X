"""SENTINEL-X - test bout en bout compatible ACL (MQTTS + utilisateur sentinel-ml).

La telemetrie synthetique est injectee directement dans la logique du service IA (meme code que
le conteneur), les SORTIES passent par le vrai broker : risque -> sentinel/ai/<device>/risk (dashboard),
alertes -> sentinel/ai/alerts (stockees par l'API), commandes -> sentinel/sentinel-node-01/cmd (ESP reel).

    docker compose run --rm --no-deps -e ML_CLIENT_ID=sentinel-ml-test -e ML_NO_STATUS=1 ml python e2e_direct.py butane
    (scenarios : butane | noise | heat)
Rien n'est ecrit dans la table telemetry.
"""
import random
import sys
import time

import service as S

scenario = sys.argv[1] if len(sys.argv) > 1 else "butane"
DEV = sys.argv[2] if len(sys.argv) > 2 else "sentinel-test-01"
svc = S.Service()
svc.client.connect(S.MQTT_HOST, S.MQTT_PORT, 30)
svc.client.loop_start()
time.sleep(1.5)
rng = random.Random(7)
T0 = time.time()
base, temp, hum = 82.0, 24.0, 45.0
up = [0]


def send(gas, t=None, h=None):
    up[0] += 2000
    d = {"device_id": DEV, "uptime_ms": up[0], "gas": int(round(gas)),
         "temperature": round(t if t is not None else temp, 1), "humidity": round(h if h is not None else hum, 1),
         "buzzer": False}
    with svc.lock:
        svc.on_telemetry(DEV, d)
        tr = svc.devices[DEV].tr
    return tr.state


def log(st, extra=""):
    print(f"  t={time.time() - T0:6.1f}s state={st:<10} siren={svc.siren_on} {extra}", flush=True)


for _ in range(70):
    send(base + rng.uniform(-3, 3))
print("[e2e] chauffe OK ; 40 s de bruit +-10 (temps reel)")
for _ in range(20):
    log(send(base + rng.uniform(-10, 10)))
    time.sleep(2)
onset = time.time()
first = None
if scenario == "butane":
    print("[e2e] SPRAY BUTANE")
    for g in [260, 480, 560, 540, 590, 520, 470, 400, 330, 270, 220, 180, 150, 125, 110, 100, 93, 88, 85]:
        st = send(g + rng.uniform(-8, 8)); log(st, f"gaz={g}")
        if st == "gaz_fumee" and first is None:
            first = time.time() - onset
        time.sleep(2)
    for _ in range(25):
        log(send(base + rng.uniform(-6, 6))); time.sleep(2)
elif scenario == "heat":
    print("[e2e] ECHAUFFEMENT +1 degC/min + gaz +3 ADC/min")
    for k in range(150):
        m = k * 2 / 60
        st = send(base + 3 * m + rng.uniform(-3, 3), temp + m, hum - 0.6 * m); log(st)
        if st in S.INCIDENTS and first is None:
            first = time.time() - onset
        time.sleep(2)
else:
    print("[e2e] bruit normal +-10 pendant 120 s")
    for _ in range(60):
        st = send(base + rng.uniform(-10, 10)); log(st)
        if st in S.INCIDENTS and first is None:
            first = time.time() - onset
        time.sleep(2)
time.sleep(2)
svc.client.loop_stop()
print(f"[e2e] premier incident : {first if first is None else round(first, 1)} s apres le debut du scenario")
