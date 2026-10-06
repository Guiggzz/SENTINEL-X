# SENTINEL-X — firmware ESP8266 (`sentinel_node`)

Carte : NodeMCU v3 (`esp8266:esp8266:nodemcuv2`, core esp8266 3.1.2, layout 4MB FS:2MB OTA:~1019KB).
Câblage : OLED SSD1306 128x64 I2C 0x3C (SDA D2 / SCL D1), buzzer passif D7, LED rouge D0, LED verte D8,
DHT22 D5, MQ-2 A0, PIR D6.

Secrets : `sentinel_node/secrets.h` (gitignoré, 0600 — modèle : `secrets.example.h`) contient Wi-Fi, MQTT,
empreinte SHA1 du certificat du broker et `OTA_PASSWORD`. Le mot de passe OTA est aussi dans
`firmware/.ota_password` (gitignoré, 0600) pour les commandes de flash ci-dessous. Ne jamais les afficher ni les commiter.

Toutes les commandes se lancent depuis la racine du dépôt (`~/sentinel-x`).

## Compiler

```bash
~/.local/bin/arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/sentinel_node
```

## Flasher par USB (câble, /dev/ttyUSB0)

Respecter la convention `firmware/.firmware.lock` (un seul flash à la fois).

```bash
sg dialout -c '~/.local/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn esp8266:esp8266:nodemcuv2 firmware/sentinel_node'
```

(`arduino-cli upload` recompile si besoin ; ajouter `--verify` pour relire le flash.)

## Flasher par Wi-Fi (OTA)

Le nœud doit déjà tourner avec un firmware OTA (ce firmware, flashé une première fois en USB) et être joignable
(IP actuelle : `172.20.10.2`, hostname mDNS `sentinel-node-01.local`, port UDP 8266). Le PC doit accepter la
connexion TCP retour de l'ESP (espota ouvre un port local : autoriser `172.20.10.2` si un pare-feu est actif).

Option A — arduino-cli (compile + upload réseau) :

```bash
~/.local/bin/arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/sentinel_node && \
~/.local/bin/arduino-cli upload --fqbn esp8266:esp8266:nodemcuv2 --protocol network --port 172.20.10.2 \
  --upload-field password="$(cat firmware/.ota_password)" firmware/sentinel_node
```

Option B — espota.py directement (port local fixe 48266 pratique pour une règle de pare-feu) :

```bash
~/.local/bin/arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 --output-dir /tmp/sentinel-build firmware/sentinel_node && \
python3 ~/.arduino15/packages/esp8266/hardware/esp8266/3.1.2/tools/espota.py \
  -i 172.20.10.2 -p 8266 -P 48266 -a "$(cat firmware/.ota_password)" -r \
  -f /tmp/sentinel-build/sentinel_node.ino.bin
```

Pendant l'OTA : buzzer coupé, MQTT fermé proprement (status `offline` retenu), contexte TLS libéré,
barre de progression sur l'OLED, puis redémarrage automatique. En cas d'échec l'ESP reprend son firmware
actuel et se reconnecte au broker.

## Vérifier la reconnexion

```bash
docker logs --since 5m sentinel-mosquitto 2>&1 | grep sentinel-node-01
# attendu : "New client connected from 172.20.10.2:… as sentinel-node-01 (p2, c1, k15, u'sentinel-node-01')."
```

Le statut retenu `sentinel/sentinel-node-01/status` repasse à `online` et la télémétrie (toutes les 500 ms)
réapparaît dans le dashboard / `GET /api/v1/telemetry`.

## Commandes MQTT (`sentinel/sentinel-node-01/cmd`, JSON)

| action | champs | effet |
|---|---|---|
| `buzzer_on` | `song`: paquetta\|rickroll\|gaz\|intrus, `duration_ms` (0 = boucle) | alarme / mélodie |
| `buzzer_off` | — | coupe le buzzer |
| `beep` | `duration_ms` (≤ 5000) | bip continu |
| `beep_pattern` | `pattern`: `identify` | double bip court (ignoré si une alarme sonne) |
| `led` | `color`: red\|green, `state`: bool | LED manuelle |
| `led_auto` | — | LEDs automatiques |
| `display` | `mode`: identify\|authorized\|intrusion\|normal, `name` (authorized), `duration_ms` 500..30000 | écran d'alerte OLED |

Modes `display` (non bloquants, base `millis()`, le nouveau mode remplace le précédent) :

- `identify` (défaut 10 s) : plein écran inversé clignotant ~2 Hz, triangle d'avertissement + « ATTENTION », « IDENTIFIEZ- / VOUS ».
- `authorized` (défaut 3 s) : « ACCES AUTORISE » + nom (accents retirés, `[A-Za-z0-9 -]`, 16 car. max).
- `intrusion` (défaut 15 s, durée de la sirène intrus) : plein écran sans aucune autre info (ni IP, ni mesures, ni bandeau),
  cycle de 1,5 s : « ATTENTION » géant (police étirée 2x5, 107 x 35 px, vidéo inverse) 0,5 s, puis
  « INTRUS » répété sur 3 lignes en taille 3 (105 x 21 px chacune, toute la hauteur) 0,5 s en vidéo normale + 0,5 s inversé.
  L'écran de télémétrie ne le recouvre pas ; il reste jusqu'à une autre commande `display` (`normal`, `authorized`…)
  ou la fin de `duration_ms`.
- `normal` : retour immédiat à l'écran de télémétrie (aussi automatique à la fin de `duration_ms`).

Exemples :

```json
{"action":"display","mode":"identify","duration_ms":8000}
{"action":"beep_pattern","pattern":"identify"}
{"action":"display","mode":"authorized","name":"Guillaume","duration_ms":3000}
{"action":"display","mode":"intrusion","duration_ms":15000}
{"action":"display","mode":"normal"}
```

Chaque commande `display` / `beep_pattern` est acquittée sur `sentinel/sentinel-node-01/alerts`
(`{"type":"actuator","state":"display_identify"}`, `beep_identify`, …).
Via l'API : `POST /api/v1/commands` avec le même JSON (validation stricte côté backend).
