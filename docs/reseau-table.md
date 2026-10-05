# Réseau table SENTINEL-X — AP dédié 192.168.10.0/24

## Objectif
Isoler le lien ESP ↔ PC sur un Wi‑Fi de table (SSID **SENTINEL-X**), indépendant du hotspot
iPhone « Guiggzz » (`172.20.10.0/28`) utilisé pour Internet / Cursor.

## Plan d’adressage

| Rôle | Adresse | Notes |
|------|---------|-------|
| PC (AP / gateway) | `192.168.10.1/24` | Interface `wlp0s20f3` en mode AP |
| ESP (DHCP) | `192.168.10.10` – `.50` | Pool dnsmasq-shared |
| Broker MQTTS | `192.168.10.1:8883` | Conteneur `sentinel-mosquitto` |
| Dashboard HTTPS | `https://192.168.10.1/` | Conteneur `sentinel-proxy` (443) |
| API | `127.0.0.1:3000` | Non exposée hors localhost |

## Profil NetworkManager (prêt, **non activé**)
- Nom de connexion : `SENTINEL-X-AP`
- SSID : `SENTINEL-X`
- Mode : AP / `ipv4.method shared` / `192.168.10.1/24`
- `autoconnect no` — n’interrompt pas le hotspot iPhone tant qu’on ne l’active pas
- DHCP : `/etc/NetworkManager/dnsmasq-shared.d/sentinel-x-table.conf` (plage `.10`–`.50`)
- PSK : stocké dans le profil NM + `firmware/sentinel_node/secrets.h` (**hors git**)

## Pourquoi l’activation est différée
Le portable n’a **qu’une** interface Wi‑Fi (`wlp0s20f3`, Intel iwlwifi). Elle est actuellement
connectée au hotspot iPhone **Guiggzz** (`172.20.10.4/28`), seul chemin Internet / agent Cursor.
Aucun dongle USB Wi‑Fi secondaire n’est présent. Activer l’AP sur `wlp0s20f3` **coupe** Guiggzz.

Option future si upstream USB iPhone : activer le partage USB sur l’iPhone (`enxf6a31072f825` /
driver `ipheth`, aujourd’hui DOWN), puis basculer le Wi‑Fi en AP tout en gardant Internet via USB.

## UFW
Règles ajoutées (en plus de `8883` depuis `172.20.10.0/28`) :
- `443/tcp` depuis `192.168.10.0/24` — SENTINEL HTTPS table AP
- `8883/tcp` depuis `192.168.10.0/24` — SENTINEL MQTTS table AP

## Switchover coordonné (quand Cursor / Internet n’est plus sur Guiggzz)

```bash
# 1) Vérifier le profil
nmcli connection show SENTINEL-X-AP

# 2) Couper Guiggzz et monter l’AP table (UNE interface — perte Internet hotspot)
nmcli connection down Guiggzz
nmcli connection up SENTINEL-X-AP

# 3) Vérifier
ip -br addr show wlp0s20f3   # attendu : 192.168.10.1/24
nmcli device wifi hotspot    # ou nmcli -f GENERAL,IP4 connection show SENTINEL-X-AP

# 4) Reflash ESP avec secrets.h déjà préparé (SSID SENTINEL-X, MQTT_HOST 192.168.10.1)
#    Seulement si /dev/ttyUSB0 présent et lock firmware respecté — voir README Flash ESP

# 5) Retour hotspot iPhone si besoin
nmcli connection down SENTINEL-X-AP
nmcli connection up Guiggzz
```

## Rollback immédiat
```bash
nmcli connection down SENTINEL-X-AP || true
nmcli connection up Guiggzz
```

## Fichiers touchés
- Profil NM `SENTINEL-X-AP` (système)
- `/etc/NetworkManager/dnsmasq-shared.d/sentinel-x-table.conf`
- `firmware/sentinel_node/secrets.h` (WIFI_* + MQTT_HOST) — **jamais commit**
- `firmware/sentinel_node/secrets.example.h` (placeholders)
- UFW règles `192.168.10.0/24`
