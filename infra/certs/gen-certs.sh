#!/usr/bin/env bash
# SENTINEL-X — génération PKI locale (CA ECDSA P-256 + certificat serveur)
# Usage: ./gen-certs.sh [--force]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

CA_KEY="ca.key"
CA_CRT="ca.crt"
SRV_KEY="server.key"
SRV_CRT="server.crt"
SRV_CSR="server.csr"
EXT_FILE="server.ext"
DAYS_CA=3650
DAYS_SRV=730

if [[ $FORCE -eq 0 && -f "$CA_KEY" && -f "$CA_CRT" && -f "$SRV_KEY" && -f "$SRV_CRT" ]]; then
  echo "[gen-certs] Certificats déjà présents (utilisez --force pour régénérer)."
  exit 0
fi

umask 077

echo "[gen-certs] Génération CA ECDSA P-256 (${DAYS_CA} jours)…"
openssl ecparam -name prime256v1 -genkey -noout -out "$CA_KEY"
openssl req -new -x509 -key "$CA_KEY" -sha256 -days "$DAYS_CA" -out "$CA_CRT" \
  -subj "/C=FR/O=SENTINEL-X/OU=EPSI-M1/CN=SENTINEL-X Local CA"

echo "[gen-certs] Génération clé + CSR serveur…"
openssl ecparam -name prime256v1 -genkey -noout -out "$SRV_KEY"
openssl req -new -key "$SRV_KEY" -out "$SRV_CSR" \
  -subj "/C=FR/O=SENTINEL-X/OU=EPSI-M1/CN=sentinel.local"

cat > "$EXT_FILE" << 'EXT'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = 172.20.10.4
IP.2 = 127.0.0.1
DNS.1 = localhost
DNS.2 = sentinel.local
DNS.3 = sentinel-mosquitto
DNS.4 = sentinel-api
DNS.5 = sentinel-proxy
EXT

echo "[gen-certs] Signature certificat serveur (${DAYS_SRV} jours)…"
openssl x509 -req -in "$SRV_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$SRV_CRT" -days "$DAYS_SRV" -sha256 -extfile "$EXT_FILE"

# Permissions : CA/clé privée 600 ; certs publics 644
# Mosquitto (eclipse-mosquitto) tourne en uid 1883 — ownership si possible
chmod 600 "$CA_KEY" "$SRV_KEY"
chmod 644 "$CA_CRT" "$SRV_CRT"
chown 1883:1883 "$SRV_KEY" "$SRV_CRT" 2>/dev/null || chmod 644 "$SRV_KEY"

openssl x509 -in "$SRV_CRT" -pubkey -noout > server_pub.pem
chmod 644 server_pub.pem

rm -f "$SRV_CSR" "$EXT_FILE" ca.srl 2>/dev/null || true

echo "[gen-certs] OK."
echo "  CA     : $SCRIPT_DIR/$CA_CRT"
echo "  Serveur: $SCRIPT_DIR/$SRV_CRT"
echo "  Pubkey : $SCRIPT_DIR/server_pub.pem (pin ESP8266)"
openssl x509 -in "$SRV_CRT" -noout -subject -dates 2>/dev/null || true
openssl x509 -in "$SRV_CRT" -noout -ext subjectAltName 2>/dev/null || \
  openssl x509 -in "$SRV_CRT" -noout -text 2>/dev/null | grep -A2 'Subject Alternative Name' || true
