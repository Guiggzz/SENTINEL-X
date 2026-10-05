#!/usr/bin/env bash
# Génère passwd Mosquitto + met à jour infra/.env (sans afficher les mots de passe)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SECRETS_DIR="$SCRIPT_DIR/secrets"
PASSWD_FILE="$SCRIPT_DIR/passwd"
ENV_FILE="$INFRA_DIR/.env"
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

gen_pw() {
  openssl rand -base64 24 | tr -d '/+=' | head -c 32
}

USERS=(sentinel-node-01 sentinel-api sentinel-ml dashboard-ro)
declare -A PWMAP

for u in "${USERS[@]}"; do
  f="$SECRETS_DIR/${u}.password"
  if [[ -f "$f" ]]; then
    PWMAP[$u]="$(cat "$f")"
  else
    PWMAP[$u]="$(gen_pw)"
    umask 077
    printf '%s' "${PWMAP[$u]}" > "$f"
    chmod 600 "$f"
  fi
done

# Générer passwd via conteneur mosquitto (mosquitto_passwd)
rm -f "$PASSWD_FILE"
touch "$PASSWD_FILE"
chmod 600 "$PASSWD_FILE"
for u in "${USERS[@]}"; do
  docker run --rm -v "$SCRIPT_DIR:/work" eclipse-mosquitto:2 \
    mosquitto_passwd -b /work/passwd "$u" "${PWMAP[$u]}"
done
# Ownership for container uid 1883
chmod 640 "$PASSWD_FILE" 2>/dev/null || chmod 600 "$PASSWD_FILE"
chown 1883:1883 "$PASSWD_FILE" 2>/dev/null || true

# Mettre à jour .env sans afficher
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # sed in-place
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}
upsert MQTT_USERNAME_API "sentinel-api"
upsert MQTT_PASSWORD_API "${PWMAP[sentinel-api]}"
upsert MQTT_USERNAME "sentinel-api"
upsert MQTT_PASSWORD "${PWMAP[sentinel-api]}"
upsert MQTT_USERNAME_ML "sentinel-ml"
upsert MQTT_PASSWORD_ML "${PWMAP[sentinel-ml]}"
upsert MQTT_USERNAME_NODE "sentinel-node-01"
upsert MQTT_PASSWORD_NODE "${PWMAP[sentinel-node-01]}"
upsert MQTT_TLS "true"
upsert MQTT_CA_FILE "/certs/ca.crt"
upsert MQTT_PORT "8883"
upsert MQTT_HOST "sentinel-mosquitto"

echo "[gen-passwd] passwd + .env mis à jour (mots de passe NON affichés)."
echo "  Fichier passwd : $PASSWD_FILE"
echo "  Secrets individuels : $SECRETS_DIR/*.password"
