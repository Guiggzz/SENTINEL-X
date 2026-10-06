#!/usr/bin/env bash
# SENTINEL-X — vérification "la démo marche encore" (sans afficher aucun secret)
# Usage : bash infra/hardening/check-demo.sh
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PW="$(cat OPERATOR_PASSWORD.txt 2>/dev/null | tr -d '\r\n')"
JAR="$(mktemp)"; trap 'rm -f "$JAR"; unset PW' EXIT
ok(){ printf '  [OK]   %s\n' "$*"; }; ko(){ printf '  [KO]   %s\n' "$*"; FAIL=1; }
FAIL=0
h=$(curl -s -m 5 http://127.0.0.1:3000/health); echo "$h" | grep -q '"status":"ok"' && ok "API /health : $h" || ko "API /health : $h"
c=$(printf '%s' "$PW" | jq -Rsc '{username:"operateur",password:.}' | curl -sk -m 5 -o /dev/null -w '%{http_code}' -c "$JAR" -H 'Content-Type: application/json' -H 'Origin: https://localhost' --data-binary @- https://localhost/api/v1/auth/login)
[ "$c" = 200 ] && ok "login via https://localhost -> $c" || ko "login via https://localhost -> $c"
c=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' -b "$JAR" https://localhost/); [ "$c" = 200 ] && ok "dashboard https://localhost/ (cookie) -> $c" || ko "dashboard -> $c"
c=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' -b "$JAR" https://localhost/api/v1/telemetry/latest); [ "$c" = 200 ] && ok "API /telemetry/latest (cookie) -> $c" || ko "telemetry -> $c"
r=$(curl -sk -m 8 -o /tmp/.sx_snap.jpg -w '%{http_code} %{content_type} %{size_download}' -b "$JAR" https://localhost/cam/snapshot.jpg); set -- $r
[ "$1" = 200 ] && [ "$3" -gt 1000 ] && ok "/cam/snapshot.jpg (cookie) -> $r" || ko "/cam/snapshot.jpg -> $r"; rm -f /tmp/.sx_snap.jpg
c=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://localhost/cam/snapshot.jpg); [ "$c" = 401 ] && ok "/cam/snapshot.jpg sans cookie -> $c" || ko "/cam sans cookie -> $c"
if echo | timeout 5 openssl s_client -connect 127.0.0.1:8883 -tls1_2 -brief </dev/null 2>&1 | grep -qiE 'Protocol version|CONNECTION ESTABLISHED'; then ok "MQTT 8883 accepte TLS 1.2"; else ko "MQTT 8883 TLS"; fi
exit $FAIL
