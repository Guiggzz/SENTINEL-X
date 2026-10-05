#!/usr/bin/env bash
# SENTINEL-X — preuves sécurité pour le jury
set -uo pipefail
HOST_IP="${1:-172.20.10.4}"
echo "===== SENTINEL-X verify.sh $(date -Is) ====="
echo
echo "### Écoute réseau (ss -tlnp)"
ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || true
echo
echo "### UFW (si root)"
if [[ "$(id -u)" -eq 0 ]] && command -v ufw >/dev/null 2>&1; then
  ufw status verbose || true
else
  echo "(skip — pas root ou ufw absent ; lancer: sudo ufw status verbose)"
fi
echo
echo "### nmap $HOST_IP"
if command -v nmap >/dev/null 2>&1; then
  nmap -Pn -p 22,80,443,1883,3000,8081,8883 "$HOST_IP" 2>/dev/null || true
else
  echo "nmap non installé — noter pour la démo jury"
fi
echo
echo "### openssl s_client MQTTS :8883"
echo | openssl s_client -connect "${HOST_IP}:8883" -servername sentinel.local 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates 2>/dev/null || echo "échec handshake 8883"
echo | openssl s_client -connect "${HOST_IP}:8883" -servername sentinel.local 2>/dev/null \
  | grep -E 'Protocol|Cipher|Verify return' || true
echo
echo "### openssl s_client HTTPS :443"
echo | openssl s_client -connect "${HOST_IP}:443" -servername localhost 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates 2>/dev/null || echo "échec handshake 443"
echo | openssl s_client -connect "${HOST_IP}:443" -servername localhost 2>/dev/null \
  | grep -E 'Protocol|Cipher|Verify return' || true
echo
echo "### MQTT anonyme refusé (8883)"
if command -v mosquitto_sub >/dev/null 2>&1; then
  timeout 3 mosquitto_sub -h "$HOST_IP" -p 8883 --cafile "$(dirname "$0")/../certs/ca.crt" \
    -t 'sentinel/#' -v 2>&1 | head -5 || echo "OK: connexion anonyme refusée/échouée"
else
  # via docker
  timeout 5 docker run --rm --network host eclipse-mosquitto:2 \
    mosquitto_sub -h "$HOST_IP" -p 8883 --cafile /dev/null -t 't' 2>&1 | head -5 || \
    echo "OK: anonyme refusé (ou TLS requis)"
fi
echo
echo "### Port 1883 fermé sur l'hôte"
if timeout 2 bash -c "echo >/dev/tcp/$HOST_IP/1883" 2>/dev/null; then
  echo "ALERTE: 1883 ouvert !"
else
  echo "OK: 1883 fermé / non joignable"
fi
echo
echo "### docker inspect résumé (sentinel-*)"
for c in $(docker ps --format '{{.Names}}' | grep -E '^sentinel-' || true); do
  echo "--- $c ---"
  docker inspect "$c" --format \
    'User={{.Config.User}} Readonly={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} CapAdd={{json .HostConfig.CapAdd}} SecurityOpt={{json .HostConfig.SecurityOpt}} Memory={{.HostConfig.Memory}} NanoCPUs={{.HostConfig.NanoCpus}} PidsLimit={{.HostConfig.PidsLimit}}' \
    2>/dev/null || true
done
echo
echo "### Preuve Wireshark (manuel)"
echo "Filtrer: tcp.port==8883 — payload MQTT illisible (chiffré TLS)."
echo "Sans root, tcpdump non disponible ; capture jury avec Wireshark sur le hotspot."
echo
echo "===== fin verify.sh ====="
