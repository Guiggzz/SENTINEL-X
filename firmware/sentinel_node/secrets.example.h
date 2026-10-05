// Copier en secrets.h et remplir. secrets.h n'est JAMAIS commité.
#pragma once
// AP table dédié SENTINEL-X (voir docs/reseau-table.md) — pas le hotspot iPhone.
#define WIFI_SSID "SENTINEL-X"
#define WIFI_PASS "mot_de_passe_wifi_table"
#define MQTT_HOST "192.168.10.1"
#define MQTT_PORT 8883
#define MQTT_USER "sentinel-node-01"
#define MQTT_PASSWORD "mot_de_passe_mqtt"
// Pin certificat serveur (SHA1) — pas de NTP requis. openssl x509 -fingerprint -sha1 -noout -in server.crt
#define MQTT_CERT_FINGERPRINT "AA:BB:CC:..."
