# SENTINEL-X — AetherCorp

Projet IoT atelier EPSI : nœud ESP8266 (firmware), broker MQTT, API FastAPI, PostgreSQL et tableau de bord web temps réel.

## Architecture

**Option technique B — Topologie distribuée « Edge-to-Server » (PC apprenant)** : le PC portable joue le rôle de *PCServeur Local* (broker MQTTS, API, PostgreSQL, proxy HTTPS, ML, vision). Le nœud ESP8266 reste en *edge* (capteurs + actionneurs) et dialogue uniquement via MQTT(S).

```
ESP8266  --MQTT-->  Mosquitto  -->  API FastAPI  -->  PostgreSQL
                         ^              |
                         |              +--> WebSocket / Dashboard
                         +-- commandes (/cmd)
```

| Conteneur            | Rôle                          | Ports hôte      |
|----------------------|-------------------------------|-----------------|
| `sentinel-mosquitto` | Broker MQTT / MQTTS           | `8883` (+ `1883` temporaire) |
| `sentinel-db`        | PostgreSQL 16 (volume nommé)  | *aucun* (réseau Docker uniquement) |
| `sentinel-api`       | FastAPI + dashboard           | `127.0.0.1:3000` |
| `sentinel-ml`        | Maintenance prédictive (Isolation Forest) | *aucun* |
| `sentinel-proxy`     | Caddy HTTPS                   | `443` |

Le PostgreSQL local de l’hôte (`127.0.0.1:5432`) n’est pas utilisé ; le conteneur DB n’expose pas de port hôte.

## Topics MQTT

| Topic | Sens | Contenu |
|-------|------|---------|
| `sentinel/{device_id}/telemetry` | ESP → broker | JSON capteurs (~2 s) |
| `sentinel/{device_id}/alerts` | ESP → broker | événements présence / actionneurs |
| `sentinel/{device_id}/status` | ESP → broker (retained) | `online` / `offline` |
| `sentinel/{device_id}/cmd` | API → ESP | commandes actionneurs |

### Exemple télémetrie

```json
{
  "device_id": "sentinel-node-01",
  "uptime_ms": 188662,
  "temperature": 23.7,
  "humidity": 55.8,
  "gas": 78,
  "presence": true,
  "buzzer": false,
  "led_red": true,
  "led_green": false,
  "rssi": -55
}
```

`temperature` / `humidity` peuvent être `null` si le DHT n’est pas prêt.

### Exemple alerte

```json
{
  "device_id": "sentinel-node-01",
  "type": "presence",
  "state": "detected",
  "uptime_ms": 123456
}
```

`type` ∈ `presence` | `actuator` ; `state` ∈ `detected` | `cleared` | `beep` | `buzzer_on` | `buzzer_off` | `led_manual` | `led_auto`.

### Commandes (`/cmd`)

```json
{"action":"buzzer_on","duration_ms":3000,"song":"gaz"}  // duration_ms 0 = boucle ; song ∈ paquetta|rickroll|gaz|intrus
{"action":"buzzer_off"}
{"action":"beep"}
{"action":"led","color":"red","state":true}
{"action":"led_auto"}
```

## API HTTP

Base : `http://localhost:3000` (aussi `http://172.20.10.4:3000` sur le Wi‑Fi équipe).

| Méthode | Chemin | Description |
|---------|--------|-------------|
| `GET` | `/health` | Santé API / MQTT / DB |
| `GET` | `/api/v1/telemetry?limit=` | Historique télémetrie |
| `GET` | `/api/v1/telemetry/latest` | Dernière mesure |
| `GET` | `/api/v1/alerts?limit=` | Journal d’événements |
| `POST` | `/api/v1/alerts` | Créer une alerte (Pydantic → Postgres → WS) |
| `GET` | `/api/v1/status` | Statuts nœuds |
| `POST` | `/api/v1/commands` | Publier une commande MQTT (`song`: paquetta/rickroll/gaz/intrus) |
| `GET` | `/api/v1/ai` | État du modèle de maintenance prédictive |
| `GET/PUT` | `/api/v1/settings/person-alarm` | Armement de l'alarme présence |
| `GET` | `/api/v1/system` | MCO hôte (CPU/RAM/disque, sondes) |
| `WS` | `/ws` | Push temps réel (telemetry / alerts / status / risk / settings) |
| `GET` | `/` | Dashboard |

## Lancer la stack

Prérequis : Docker (utilisateur dans le groupe `docker`), sans `sudo`.

```bash
cd ~/sentinel-x/infra
# Copier et renseigner les secrets si besoin
cp -n .env.example .env
chmod 600 .env

docker compose up -d --build
docker compose ps
```

Arrêt :

```bash
cd ~/sentinel-x/infra && docker compose down
```

Les volumes `mosquitto-data` et `sentinel-pgdata` sont conservés.

## Dashboard

Interface française type centre de commande (AetherCorp / SENTINEL-X) :

- graphiques température / humidité / gaz (~5 min) ;
- cartes statut (nœud, présence, buzzer, LEDs, RSSI) ;
- journal d’événements ;
- boutons de commande ;
- panneau **Flux caméra IA** (MJPEG YOLO depuis le service vision, défaut `:8081/stream.mjpg`).



## Service vision (webcam + YOLO)

Détection de personnes sur la webcam du PC (`BisonCam` / `/dev/video0`) avec **YOLOv8n** (CPU), flux MJPEG pour le dashboard.

| Endpoint | Description |
|----------|-------------|
| `http://localhost:8081/stream.mjpg` | Flux MJPEG annoté |
| `http://localhost:8081/snapshot.jpg` | Instantané JPEG |
| `http://localhost:8081/health` | JSON (`camera_ok`, `fps`, `persons`, …) |

Aussi accessible en LAN : `http://172.20.10.4:8081/…`.

### systemd --user

```bash
systemctl --user enable --now sentinel-vision.service
systemctl --user status sentinel-vision.service
systemctl --user stop sentinel-vision.service
journalctl --user -u sentinel-vision.service -f
```

Répertoire : `~/sentinel-x/vision` (venv `.venv`, modèle `yolov8n.pt`).
Alertes `type=vision` / `state=person_detected|person_cleared` → `POST /api/v1/alerts` (device_id `sentinel-cam-01`).

## IA — maintenance prédictive (conteneur `sentinel-ml`)

Aucune règle statique du type `if temp > 40` : un modèle **Isolation Forest** (scikit-learn) apprend
la dynamique *normale* des capteurs et signale les **anomalies cinétiques** (dérives, montées rapides,
corrélations suspectes). Les seuils de décision sont **appris** (quantiles du score sur l'historique
normal), pas fixés sur une valeur capteur.

**Flux** : `sentinel/+/telemetry` → `sentinel-ml` → `sentinel/ai/<device>/risk` (risque 0-100, état) +
`sentinel/ai/alerts` (incidents, stockés par l'API) + `sentinel/sentinel-node-01/cmd` (sirène / LED).
Le statut du service est publié en LWT sur `sentinel/ai/status` (`online`/`offline`).
Topics choisis pour respecter l'ACL Mosquitto de l'utilisateur `sentinel-ml`.

### Features (une fenêtre par mesure, cadence ESP 2 s)

| Feature | Fenêtre | Ce qu'elle capte |
|---|---|---|
| `gas_dev_rel` | ligne de base EWMA ~5 min | écart relatif du gaz à sa ligne de base (micro-déviation, fuite) |
| `gas_slope_30s` | 30 s | pente du gaz (spray butane : montée brutale) |
| `gas_std_30s` | 30 s | turbulence du panache |
| `gas_slope_2m` | 2 min | dérive lente du gaz |
| `temp_slope_2m` | 2 min | échauffement lent (°C/min) |
| `temp_dev` | ligne de base EWMA ~10 min | écart de température |
| `hum_slope_2m` | 2 min | variation d'humidité (assèchement à l'échauffement) |
| `gas_temp_corr` | 2 min | corrélation gaz × température (signature « hausse lente de T° + micro-déviation gaz ») |

En anomalie, la ligne de base n'apprend presque plus (×0,05) pour ne pas « absorber » la fuite.
Au redémarrage de l'ESP (uptime qui repart à 0), l'état est réinitialisé (chauffe MQ-2).

**Features directionnelles** : avant d'entrer dans l'Isolation Forest, `vectorize()` ne conserve que le
sens du danger (hausse de gaz, échauffement, chute d'humidité, corrélation gaz×température positive).
Les baisses et retours à la normale sont mis à zéro (neutre) : la forêt, symétrique par nature, ne
traite plus une décroissance bénigne (gaz qui redescend après un essai, refroidissement) comme une anomalie.

### Décision

1. score d'anomalie = `-IsolationForest.score_samples()` sur features standardisées ;
2. **risque 0-100** : interpolation par morceaux entre la médiane normale (0), la vigilance q98 (30),
   le seuil d'anomalie appris via `contamination` (50) et le score d'un incident extrême de référence (100) ;
3. **type d'incident** par contribution des features (z-scores robustes médiane/MAD appris) :
   groupe gaz dominant → `gaz_fumee`, groupe thermique dominant → `thermique`, sinon `anomalie` ;
4. **persistance** : 4 fenêtres anormales consécutives (≈ 8 s) pour lever l'incident, 5 normales pour le clore ;
   un incident `gaz_fumee`/`thermique` se termine aussi dès que sa signature ne domine plus pendant 5 fenêtres
   (ex. gaz revenu à sa base mais température qui redescend après un souffle) : la sirène s'arrête,
   un écart résiduel éventuel est journalisé en `anomalie` (sans son) ;
   les relevés DHT22 aberrants isolés sont filtrés (médiane 3) ;
5. **prédiction** (pré-alerte sans son) : risque lissé en zone de vigilance avec tendance haussière,
   ou premières fenêtres anormales non encore confirmées — uniquement si la déviation va dans le sens
   d'un danger (groupe gaz en hausse ou échauffement) ; une baisse du gaz après un pic ne déclenche rien.

### Actions

| État | Action |
|---|---|
| `prediction` | alerte `ia_prediction/risque_croissant` (journal + dashboard), **sans son** |
| `gaz_fumee` | alerte `ia_gaz/gaz_fumee_detecte` (avec risque/score/features) + `buzzer_on` **song `gaz`** en boucle (`duration_ms: 0`) + LED rouge fixe. Fin : `ia_gaz/gaz_fumee_termine`, `buzzer_off`, `led_auto` |
| `thermique` | alerte `ia_thermique/derive_thermique_detectee` + LED rouge, bandeau sur le dashboard |
| `anomalie` | alerte `ia_anomalie/anomalie_cinetique` (journal + dashboard) |

« Couper alarme » pendant une alerte gaz : la télémétrie (`buzzer=false`) sert d'acquittement ; la sirène
ne repart pas avant **60 s** (`ML_REARM_S`) si le gaz est toujours présent. L'alarme gaz sonne
**toujours**, indépendamment de l'interrupteur « Alarme présence », et reste prioritaire sur la sirène intrus.

### Entraînement / réentraînement

Données : historique **normal** de la table `telemetry` (`sentinel-node-01`). Les périodes non normales
(préchauffage, essais briquet / chauffage DHT22, capteur débranché, rechauffes post-reboot) sont exclues
via `ml/training_exclusions.json` (étiquetage manuel, horaires UTC). Le jeu inclut les plages calmes du
matin **et** de l'après-midi (après rebranchement MQ-2 et stabilisation de la baseline). Split temporel :
80 % entraînement, 20 % les plus récents pour l'évaluation.
Augmentation : bruit capteur réaliste (MQ-2 ±10 ADC, DHT22 ±0,5 °C / ±2 %RH) + scénarios bénins
« personne proche » (respiration +2..8 %RH, +0,3..0,8 °C lent) traités comme normaux.

```bash
cd ~/sentinel-x/infra
docker compose run --rm --no-deps ml python train.py        # entraîne + évalue
docker compose run --rm --no-deps ml python train.py --eval-only
docker compose restart ml                                   # recharge le modèle
```

Sorties : `ml/models/model.joblib` (scaler + Isolation Forest), `ml/models/metadata.json`
(taille d'entraînement, contamination, liste des features, date, calibration, périodes exclues),
`ml/evaluation.md` (résultats).

### Évaluation (extrait de `ml/evaluation.md`)

Genere le 2026-10-05T12:33:44+00:00 par `ml/train.py` (graine 42).

#### Modele

- Isolation Forest scikit-learn, 300 arbres, contamination 0.005
- Entrainement : 691 fenetres reelles (sentinel-node-01, 2026-10-05T09:40:30.622191+00:00 -> 2026-10-05T10:13:48.500096+00:00 UTC) + 691 fenetres augmentees (bruit capteur +-10 ADC)
- Features (8) : gas_dev_rel, gas_slope_30s, gas_std_30s, gas_slope_2m, temp_slope_2m, temp_dev, hum_slope_2m, gas_temp_corr
- Calibration du score (appris) : mediane 0.444, vigilance q98 0.555 (risque 30), seuil anomalie 0.580 (risque 50), incident extreme 0.679 (risque 100)
- Decision : 3 fenetres anormales consecutives (6 s) ; fin d'incident apres 5 fenetres normales

#### 1. Donnees normales (holdout temporel)

| Jeu | Fenetres | Duree | Fenetres > seuil | Incidents (faux positifs) | Episodes 'prediction' | Risque median / p99 |
|---|---|---|---|---|---|---|
| Normal reel | 156 | 0.09 h | 0 (0.00 %) | 0 | 0 | 0.0 / 4.1 |
| Normal + bruit gaz +-10 | 156 | 0.09 h | 0 | 0 | - | - |

#### 2. Spray de gaz butane simule (cas de la demo)

20 injections : gaz de ~80 a 200-600 ADC en 2-4 s, plateau 10-30 s, decroissance ~60 s.

- Detectes en `gaz_fumee` en <= 10 s : **20/20** (rappel 100 %)
- Latence de declenchement (debut du spray -> alarme) : mediane 6 s, max 6 s

- Pics isoles d'un seul echantillon (300-1024 ADC, glitch) : **0/20** declenchements (attendu 0)

#### 3. Detection precoce (derives lentes) vs regle statique

Comparaison avec ce que ferait une regle statique de type `if gaz > 300` ou `if temp > 40` : on mesure l'avance de l'IA (premiere `prediction` et incident confirme).

| Scenario | Prediction a | Incident a (type) | Regle statique atteinte a | Avance IA |
|---|---|---|---|---|
| Fuite lente +20 ADC/min | 1.0 min | 2.5 min (gaz_fumee) | 11.1 min (gaz > 300) | 10.1 min |
| Fuite lente +8 ADC/min | 6.6 min | non (-) | 27.8 min (gaz > 300) | 21.2 min |
| Echauffement +0.5 degC/min + gaz +2 ADC/min | 1.5 min | 1.9 min (thermique) | 26.7 min (temp > 40 degC) | 25.2 min |
| Echauffement +0.3 degC/min + gaz +1 ADC/min | 3.4 min | 3.6 min (thermique) | 44.5 min (temp > 40 degC) | 41.1 min |

#### 4. Rejeu de l'historique reel (pics de gaz reellement enregistres)

Rejeu en flux de 1470 mesures reelles (11:40 -> 12:31, heure de Paris), incluant les pics de gaz exclus de l'entrainement.

| Debut incident / escalade (Paris) | Type | Gaz brut | Risque |
|---|---|---|---|
| 12:01:41 | anomalie | 88 | 58 |
| 12:03:03 | thermique | 75 | 69 |
| 12:05:27 | anomalie | 90 | 78 |
| 12:05:53 | thermique | 78 | 52 |
| 12:14:26 | gaz_fumee | 1024 | 60 |
| 12:16:32 | anomalie | 110 | 81 |
| 12:20:46 | anomalie | 89 | 55 |

Episodes `prediction` (pre-alerte sans son) : 5.

#### Synthese

- Rappel spray butane (<= 10 s) : 100 %
- Faux incidents sur 0.09 h de donnees normales : 0 (0.00 / h)
- Precision approchee (incidents vrais / incidents leves) : 100 %
- Derives lentes detectees avant la regle statique : 4/4
- Pics isoles (glitch) ignores : 20/20

> Limites : ~35 min de données normales réelles seulement (session du 05/10 matin) ; le holdout normal
> ne couvre que 0,09 h. Dans le rejeu réel, les épisodes `thermique` / `anomalie` correspondent à de vraies
> variations de température/gaz pendant les essais : à considérer « à vérifier », pas comme des fuites.
> Réentraîner après avoir enregistré plusieurs heures de fonctionnement normal (étiqueter les essais
> dans `ml/training_exclusions.json`).

### Test sans polluer l'historique

Le broker n'expose plus que 8883 (TLS + ACL) : un client de test ne peut pas publier de télémétrie.
`ml/e2e_direct.py` injecte donc une télémétrie synthétique **directement dans la logique du service**
(sans passer par `/telemetry`, rien n'est écrit dans la table `telemetry`) et publie les sorties
(risque, alertes, commandes) sur le **vrai broker** avec le compte `sentinel-ml` : l'ESP réagit réellement.

```bash
cd ~/sentinel-x/infra
docker compose run --rm --no-deps -e ML_CLIENT_ID=sentinel-ml-test -e ML_NO_STATUS=1 \
  ml python e2e_direct.py butane            # ou : noise | heat  [device_id, défaut sentinel-test-05]
```

Rejouer une plage réelle de l'historique (heures UTC) avec le modèle courant :
`docker compose run --rm --no-deps ml python replay_window.py 2026-10-05T12:00:00 2026-10-05T12:40:00`

`ml/inject_test.py` (injection MQTT de télémétrie sur `sentinel-test-01`) nécessite un broker autorisant
la publication de télémétrie de test (ancien listener 1883) ; il n'est plus utilisable avec l'ACL actuelle.

Écart assumé : le topic IA demandé `sentinel/sentinel-node-01/ai` est publié sous
`sentinel/ai/<device>/risk` (alertes IA sur `sentinel/ai/alerts`, statut/LWT sur `sentinel/ai/status`)
pour rester dans l'ACL `sentinel-ml` (écriture `sentinel/ai/#` et `sentinel/+/cmd` uniquement).

## Alarmes sonores (firmware)

| `song` | Usage | Motif |
|---|---|---|
| `gaz` | alarme IA gaz/fumée | sirène deux tons 1900/1300 Hz + 2 balayages montants 1000→2700 Hz, cycle 2,5 s, son continu |
| `intrus` | alarme présence | triple bip sec 3100 Hz puis pause, cycle 0,8 s |
| `rickroll`, `paquetta` | boutons du dashboard | mélodies existantes |

`{"action":"buzzer_on","song":"gaz","duration_ms":0}` boucle jusqu'à `buzzer_off` (`duration_ms` > 0 = durée).
L'OLED affiche `!! ALERTE GAZ !!` / `!! INTRUSION !!`. Motifs dans `firmware/sentinel_node/alarms.h`.

## Alarme présence (armement)

- Réglage persistant en base (table `app_settings`, clé `person_alarm_enabled`, **désarmée par défaut**).
- `GET /api/v1/settings/person-alarm` → `{"enabled": false, "duration_ms": 15000, "song": "intrus"}`
- `PUT /api/v1/settings/person-alarm` `{"enabled": true}` → diffusé sur `/ws` (`channel: "settings"`) + journal `reglage/alarme_presence_armee`.
- Quand le service vision publie `person_detected` : **armée** → `buzzer_on` `song: intrus` 15 s
  (`PERSON_ALARM_DURATION_MS`) + alerte `intrusion/alarme_declenchee` ; `person_cleared` coupe la sirène intrus.
  **Désarmée** → la détection est seulement journalisée. Pendant une alerte gaz, pas de sirène intrus
  (`intrusion/detectee_pendant_alarme_gaz`). Le PIR (instable) n'est pas utilisé.

## Nouveaux endpoints / dashboard

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/ai` | dernier état IA par équipement + historique du risque (15 min) |
| `GET/PUT` | `/api/v1/settings/person-alarm` | armement de l'alarme présence |
| `GET` | `/api/v1/system` | MCO : CPU/RAM/disque de l'hôte (`/proc` lu depuis le conteneur API), débit MQTT, sondes mosquitto / db / ml / nœud |

Canaux WebSocket ajoutés : `risk` (score IA temps réel), `settings`.
Dashboard : bandeau **ALERTE GAZ / FUMÉE**, bloc **Analyse IA** (risque, courbe, état, modèle),
bloc **Vision IA** (temps d'inférence YOLO/trame, `infer_ms` de `/cam/health`), bloc **MCO**,
interrupteur **Alarme présence**, boutons de test des sirènes `gaz` / `intrus`.
Le service vision expose `infer_ms`, `infer_ms_max`, `infer_frame` dans `/health` et l'affiche en incrustation.

### Variables d'environnement

| Service | Variables |
|---|---|
| `sentinel-ml` | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` (compose : `MQTT_USERNAME_ML` / `MQTT_PASSWORD_ML`), `MQTT_TLS`, `MQTT_CA_FILE`, `MQTT_TELEMETRY_TOPIC`, `DATABASE_URL`, `ML_ACTUATOR_DEVICE`, `ML_TRAIN_DEVICE`, `ML_GAS_BUZZER_MS`, `ML_REARM_S`, `ML_CONTAMINATION`, `ML_AI_TOPIC`, `ML_ALERTS_TOPIC`, `ML_STATUS_TOPIC` |
| vision | `SENTINEL_API_ALERTS` (ou `API_BASE_URL`), `SENTINEL_API_TOKEN` (ou `API_TOKEN`) → en-tête `Authorization: Bearer` |
| API | `PERSON_ALARM_DURATION_MS` (défaut 15000) |


## Sécurité

### URLs
- Dashboard HTTPS : `https://localhost/` et `https://172.20.10.4/` (port **443** ; le port 80 de l'hôte est déjà pris par Apache — pas de redirect HTTP SENTINEL)
- MQTTS : `172.20.10.4:8883` (TLS)
- MQTT plain `1883` : **temporaire** (hotspot only, ESP non flashé) — à fermer après flash MQTTS
- Caméra : `/cam/stream.mjpg` (même origine HTTPS)

### Import CA (navigateur)
Certificat signé par la CA locale `infra/certs/ca.crt`.
- **Firefox** : Paramètres → Vie privée et sécurité → Certificats → Autorités → Importer `ca.crt` → cocher « Confirmer les sites web ».
- **Chrome/Chromium** : `chrome://settings/certificates` → Autorités → Importer `~/sentinel-x/infra/certs/ca.crt`.

### Compte opérateur
- Identifiant : **operateur**
- Mot de passe : fichier `infra/OPERATOR_PASSWORD.txt` (chmod 600, hors git) — **ne pas committer**

### Clients machine (Bearer)
- `API_TOKEN` dans `infra/.env` ; vision : `~/.config/sentinel/vision.env`

### Régénérer les certificats
```bash
cd ~/sentinel-x/infra/certs && ./gen-certs.sh --force
# Mettre à jour le pin ESP (server_pub.pem → secrets.h) puis reflash
cd ~/sentinel-x/infra && docker compose restart sentinel-mosquitto sentinel-proxy
```

### Rotation mots de passe MQTT
```bash
rm -f ~/sentinel-x/infra/mosquitto/secrets/*.password
bash ~/sentinel-x/infra/mosquitto/gen-passwd.sh
# Mettre à jour firmware/secrets.h + redémarrer stack
```

### Preuves / vérification
```bash
bash ~/sentinel-x/infra/hardening/verify.sh | tee ~/sentinel-x/docs/preuves-securite.txt
```

### Durcissement hôte
```bash
pkexec bash ~/sentinel-x/infra/hardening/harden.sh
# log : infra/hardening/harden.log
```

### Flash ESP MQTTS (quand /dev/ttyUSB0 présent)
```bash
# Respecter le lock firmware
export PATH=$HOME/.local/bin:$PATH
cd ~/sentinel-x/firmware
# créer .firmware.lock, compiler, flasher, retirer lock
# Puis retirer le listener 1883 de mosquitto + port publié
```

### Choix TLS ESP
Pin du certificat serveur (**SHA1 fingerprint** via BearSSL `setFingerprint` / `MQTT_CERT_FINGERPRINT`) — robuste hors-ligne sans NTP (préféré à `setKnownKey` sur ESP8266).


## Démo jury (étapes rapides)

1. **Stack** : `cd ~/sentinel-x/infra && docker compose up -d --build` puis `docker compose ps` (ne pas toucher aux conteneurs `healthai-*`).
2. **HTTPS** : ouvrir `https://localhost/` (importer `infra/certs/ca.crt` si besoin) — dashboard opérateur.
3. **Vision** : `systemctl --user start sentinel-vision.service` ; flux `/cam/stream.mjpg` via le proxy.
4. **MQTT / ESP** : nœud `sentinel-node-01` sur MQTTS `:8883` (mot de passe dans `firmware/.../secrets.h`, **hors git** — partir de `secrets.example.h`).
5. **Credentials (placeholders)** :
   - Opérateur dashboard : identifiant `operateur` / mot de passe dans `infra/OPERATOR_PASSWORD.txt` (généré, hors git).
   - MQTT clients : fichiers `infra/mosquitto/secrets/*.password` (hors git) ; régénérer avec `infra/mosquitto/gen-passwd.sh`.
   - Variables d'environnement : copier `infra/.env.example` → `infra/.env` (jamais committer `.env`).
6. **IA** : panneau risque sur le dashboard ; rejouer / entraîner via `docker compose run --rm --no-deps ml python train.py`.
7. **Scénario gaz** : spray butane contrôlé → incident `gaz_fumee` + sirène ; « Couper alarme » pour acquitter.
8. **Preuves sécu** : `bash infra/hardening/verify.sh` ; SSH clé-only (voir `docs/preuves-securite.txt`).

Ports utiles : **443** (HTTPS), **8883** (MQTTS, idéalement LAN/hotspot), **8081** (vision, restreint UFW), API bind `127.0.0.1:3000` (pas exposée).


## Structure

```
sentinel-x/
├── firmware/          # ESP8266 (alarms.h = sirènes gaz/intrus)
├── backend/           # FastAPI + static dashboard
├── ml/                # Isolation Forest (train.py, service.py, models/, evaluation.md)
├── infra/             # docker-compose, mosquitto, certs, .env
├── vision/            # Webcam + YOLO (MJPEG :8081, infer_ms)
└── README.md
```

**Ne jamais committer** `infra/.env` ni `firmware/sentinel_node/secrets.h`.

### Caméra utilisée
Le service lit la webcam **UGREEN** via son chemin stable
`/dev/v4l/by-id/usb-UGREEN_Camera_UGREEN_Camera_SN0001-video-index0`,
capturée en 1280x720 (MJPEG) puis redimensionnée en **440p (782x440)**.
Réglages dans `~/.config/systemd/user/sentinel-vision.service` :
`SENTINEL_CAMERA_DEVICE`, `SENTINEL_CAPTURE_WIDTH/HEIGHT`, `SENTINEL_OUTPUT_HEIGHT`.
Après modification : `systemctl --user daemon-reload && systemctl --user restart sentinel-vision`.
