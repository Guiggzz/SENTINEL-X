"""SENTINEL-X - injection de telemetrie synthetique pour tester l'IA sans polluer l'historique reel.

Publie sur sentinel/<TEST_DEVICE>/telemetry (defaut sentinel-test-01, filtre a l'entrainement),
puis observe sentinel/ai/<device>/risk, les alertes et les commandes envoyees a l'actionneur.

    docker compose run --rm sentinel-ml python inject_test.py butane   # spray briquet
    docker compose run --rm sentinel-ml python inject_test.py noise    # bruit normal +-10 (ne doit rien declencher)
    docker compose run --rm sentinel-ml python inject_test.py heat     # echauffement lent + micro-deviation gaz
Nettoyage conseille ensuite : DELETE FROM telemetry WHERE device_id LIKE 'sentinel-test-%';
"""
from __future__ import annotations

import json
import random
import sys
import time

import paho.mqtt.client as mqtt

import service as S

DEV = "sentinel-test-01"
scenario = sys.argv[1] if len(sys.argv) > 1 else "butane"
if len(sys.argv) > 2:
    DEV = sys.argv[2]
T0 = time.time()
events: list[tuple[float, str, str]] = []


def on_msg(c, u, m):
    try:
        d = json.loads(m.payload)
    except Exception:
        d = m.payload.decode()
    t = time.time() - T0
    if m.topic == S.AI_TOPIC_FMT.format(device=DEV):
        if isinstance(d, dict) and d.get("state") != "warmup":
            print(f"  t={t:6.1f}s AI state={d['state']:<10} risk={d.get('risk'):>5} cand={d.get('candidate')} alarm={d.get('alarm')}", flush=True)
            events.append((t, "ai", d["state"]))
    else:
        print(f"  t={t:6.1f}s {m.topic} {d}", flush=True)
        events.append((t, m.topic, json.dumps(d)))


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sentinel-inject-{random.randint(0, 9999)}")
if S.MQTT_USERNAME:
    c.username_pw_set(S.MQTT_USERNAME, S.MQTT_PASSWORD)
if S.MQTT_TLS:
    import ssl
    c.tls_set(ca_certs=S.MQTT_CA_FILE, cert_reqs=ssl.CERT_REQUIRED)
c.on_message = on_msg
c.connect(S.MQTT_HOST, S.MQTT_PORT, 30)
c.subscribe([(S.AI_TOPIC_FMT.format(device=DEV), 0), (S.T_ALERTS, 0), (S.T_CMD, 0), (f"sentinel/{S.ACTUATOR}/alerts", 0)])
c.loop_start()

rng = random.Random(7)
temp, hum, base = 24.0, 45.0, 82.0


def send(gas, t=None, h=None):
    c.publish(f"sentinel/{DEV}/telemetry", json.dumps({
        "device_id": DEV, "uptime_ms": int((time.time() - T0) * 1000), "temperature": round(t if t is not None else temp, 1),
        "humidity": round(h if h is not None else hum, 1), "gas": int(round(gas)), "presence": False,
        "buzzer": False, "led_red": False, "led_green": True, "rssi": -60, "test": True}))


print(f"[inject] chauffe rapide {DEV} (70 mesures normales)")
for _ in range(70):
    send(base + rng.uniform(-3, 3))
    time.sleep(0.05)
print("[inject] 40 s de bruit normal +-10 en temps reel (2 s)")
for _ in range(20):
    send(base + rng.uniform(-10, 10))
    time.sleep(2)

onset = None
if scenario == "butane":
    onset = time.time() - T0
    print(f"[inject] t={onset:.1f}s SPRAY BUTANE")
    seq = [260, 480, 560, 540, 590, 520, 470, 400, 330, 270, 220, 180, 150, 125, 110, 100, 93, 88, 85]
    for g in seq:
        send(g + rng.uniform(-8, 8))
        time.sleep(2)
    for _ in range(25):
        send(base + rng.uniform(-6, 6))
        time.sleep(2)
elif scenario == "heat":
    onset = time.time() - T0
    print(f"[inject] t={onset:.1f}s ECHAUFFEMENT +1 degC/min + gaz +3 ADC/min (5 min)")
    for k in range(150):
        m = k * 2 / 60
        send(base + 3 * m + rng.uniform(-3, 3), temp + 1.0 * m + rng.uniform(-0.05, 0.05), hum - 0.6 * m)
        time.sleep(2)
    for _ in range(25):
        send(base + rng.uniform(-3, 3))
        time.sleep(2)
else:
    print("[inject] bruit normal +-10 pendant 120 s")
    for _ in range(60):
        send(base + rng.uniform(-10, 10))
        time.sleep(2)
time.sleep(3)
c.loop_stop()
inc = [e for e in events if e[1] == "ai" and e[2] in S.INCIDENTS]
cmds = [e for e in events if e[1] == S.T_CMD]
print("\n[resume]")
if onset is not None and inc:
    print(f"  incident '{inc[0][2]}' leve {inc[0][0] - onset:.1f} s apres le debut du scenario")
print(f"  incidents IA : {len(inc)} fenetres ; commandes actionneur : {[e[2] for e in cmds]}")
