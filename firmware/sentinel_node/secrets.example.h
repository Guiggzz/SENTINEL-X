// Copier en secrets.h et remplir. secrets.h n'est JAMAIS commité.
#pragma once
#define WIFI_SSID "nom_du_wifi"
#define WIFI_PASS "mot_de_passe"
#define MQTT_HOST "172.20.10.4"
#define MQTT_PORT 8883
#define MQTT_USER "sentinel-node-01"
#define MQTT_PASSWORD "mot_de_passe_mqtt"
// Pin certificat serveur (SHA1) — pas de NTP requis. openssl x509 -fingerprint -sha1 -noout -in server.crt
#define MQTT_CERT_FINGERPRINT "AA:BB:CC:..."
