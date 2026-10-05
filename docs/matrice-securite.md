# Matrice de sécurité SENTINEL-X

| Menace | Vecteur (jury/pentest) | Impact | Mitigation implémentée | Risque résiduel |
|--------|------------------------|--------|------------------------|-----------------|
| MitM / sniffing MQTT | Wireshark sur hotspot | Lecture télémétrie, injection cmd | MQTTS TLS 1.2+ (port 8883), cert SAN, auth user/ACL | CA auto-signée (navigateur) ; ESP pin SHA1 fingerprint MQTT_CERT_FINGERPRINT |
| Injection payload MQTT | Client rogue / Metasploit | Commandes ESP, fausse alerte | `allow_anonymous false`, ACL par rôle, passwords forts | Compromission d'un secret MQTT |
| DoS broker | Flood connexions/messages | Indispo monitoring | max_connections, max_inflight/queued, message_size_limit, docker logging rotation | DoS L2/L3 hors MQTT |
| Brute force API | POST login / commands | Prise de contrôle dashboard | Session HttpOnly Secure, rate-limit login/commands, API_TOKEN Bearer | Mot de passe opérateur faible si non roté |
| Exposition services | Nmap ports ouverts | Surface d'attaque | UFW + DOCKER-USER, 1883/3000/8081 non exposés LAN, HTTPS seul | Bypass si bind 0.0.0.0 réintroduit |
| Accès physique ESP | Vol / flash dump | Secrets WiFi/MQTT dans flash | secrets.h hors git, TLS + user dédié ACL | Flash readable si accès physique |
| Conteneur escape | CVE / misconfig Docker | Accès hôte | non-root, read_only, cap_drop ALL, no-new-privileges, limits | Docker rootful (recommandation rootless) |
| Rogue client MQTT | Connexion avec mauvais user | Lecture/écriture hors périmètre | ACL stricte par user | Fuite password fichier .env |

## Preuves

Voir `docs/preuves-securite.txt` (sortie de `infra/hardening/verify.sh`).
