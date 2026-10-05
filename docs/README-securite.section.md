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
