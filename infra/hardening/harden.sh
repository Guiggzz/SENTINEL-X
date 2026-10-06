#!/usr/bin/env bash
# SENTINEL-X — durcissement hôte (idempotent, commentaires FR)
# NOTE (2026-10-06) : remplacé par infra/harden-root.sh (idempotent, SANS "ufw reset"). Ne plus relancer ce script.
# Exécuter via: pkexec bash ~/sentinel-x/infra/hardening/harden.sh
# Ne coupe PAS le hotspot iPhone ni Docker existant (healthai inclus).
set -euo pipefail

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$LOG_DIR/harden.log"
HOTSPOT_SUBNET="172.20.10.0/28"
exec > >(tee -a "$LOG") 2>&1

echo "===== SENTINEL-X harden.sh $(date -Is) ====="
if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERREUR: doit tourner en root (pkexec)."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# --- 1. UFW ---
if ! command -v ufw >/dev/null 2>&1; then
  apt-get update -y || true
  apt-get install -y ufw || true
fi

if command -v ufw >/dev/null 2>&1; then
  echo "[ufw] Configuration…"
  ufw --force reset || true
  ufw default deny incoming
  ufw default allow outgoing
  # HTTPS / redirect HTTP
  ufw allow 443/tcp comment 'SENTINEL HTTPS'
  # Vision: autoriser bridges Docker puis deny le reste (proxy Caddy -> host.docker.internal)
  ufw allow from 172.16.0.0/12 to any port 8081 proto tcp comment 'SENTINEL vision docker' || true
  ufw deny 8081/tcp comment 'SENTINEL vision deny others' || true
  ufw deny 3000/tcp comment 'SENTINEL api deny' || true
  ufw allow 80/tcp comment 'SENTINEL HTTP redirect'
  # MQTTS depuis le hotspot uniquement
  ufw allow from "$HOTSPOT_SUBNET" to any port 8883 proto tcp comment 'SENTINEL MQTTS hotspot'
  # 1883 plain retiré après flash MQTTS
  # HTTP 80: apache hôte déjà présent — ne pas casser
  ufw allow 80/tcp comment 'HTTP (apache hôte / redirect)' || true
  # SSH seulement si openssh-server installé
  if dpkg -l openssh-server 2>/dev/null | grep -q '^ii'; then
    ufw allow 22/tcp comment 'SSH'
  fi
  # Ne pas bloquer le trafic Docker interne / established
  ufw --force enable
  echo "[ufw] status:"
  ufw status verbose || true
else
  echo "[ufw] non disponible — ignoré"
fi

# --- 2. DOCKER-USER : appliquer la politique aux ports publiés Docker ---
# Docker bypass UFW via la chaîne FORWARD ; on filtre dans DOCKER-USER.
AFTER_RULES="/etc/ufw/after.rules"
MARKER_BEGIN="# BEGIN SENTINEL-X DOCKER-USER"
MARKER_END="# END SENTINEL-X DOCKER-USER"

DOCKER_USER_BLOCK=$(cat << BLOCK
$MARKER_BEGIN
*filter
:DOCKER-USER - [0:0]
# Conserver le trafic établi / lié
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
# MQTTS 8883 : uniquement depuis hotspot
-A DOCKER-USER -p tcp --dport 8883 -s $HOTSPOT_SUBNET -j RETURN
-A DOCKER-USER -p tcp --dport 8883 -j DROP
# HTTPS 443 / HTTP 80 : ouverts (jury + LAN demo)
-A DOCKER-USER -p tcp --dport 443 -j RETURN
-A DOCKER-USER -p tcp --dport 80 -j RETURN
# Ne PAS toucher aux autres ports Docker (healthai-loki/promtail etc.)
-A DOCKER-USER -j RETURN
COMMIT
$MARKER_END
BLOCK
)

if [[ -f "$AFTER_RULES" ]]; then
  if grep -q "$MARKER_BEGIN" "$AFTER_RULES"; then
    # Remplacer le bloc existant
    tmp="$(mktemp)"
    awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
      $0==b {skip=1; next}
      $0==e {skip=0; next}
      !skip {print}
    ' "$AFTER_RULES" > "$tmp"
    cat "$tmp" > "$AFTER_RULES"
    rm -f "$tmp"
  fi
  printf '\n%s\n' "$DOCKER_USER_BLOCK" >> "$AFTER_RULES"
  echo "[docker-user] Bloc SENTINEL-X ajouté dans $AFTER_RULES"
  ufw reload || true
else
  echo "[docker-user] $AFTER_RULES absent — création partielle"
  mkdir -p /etc/ufw
  printf '%s\n' "$DOCKER_USER_BLOCK" >> "$AFTER_RULES"
fi

# Appliquer aussi immédiatement via iptables si disponible (sans attendre reboot)
if command -v iptables >/dev/null 2>&1; then
  # Créer chaîne si besoin
  iptables -nL DOCKER-USER >/dev/null 2>&1 || iptables -N DOCKER-USER 2>/dev/null || true
  # Règles idempotentes : flush uniquement nos commentaires via insert en tête si absentes
  if ! iptables -nL DOCKER-USER 2>/dev/null | grep -q 'SENTINEL8883'; then
    iptables -I DOCKER-USER 1 -p tcp --dport 8883 ! -s "$HOTSPOT_SUBNET" -j DROP -m comment --comment SENTINEL8883 2>/dev/null || true
  fi
  echo "[iptables] règle DOCKER-USER MQTTS appliquée (best-effort)"
fi

# --- 3. SSH ---
if dpkg -l openssh-server 2>/dev/null | grep -q '^ii'; then
  echo "[ssh] openssh-server présent — durcissement"
  TARGET_USER="guiggzz"
  SSH_DIR="/home/$TARGET_USER/.ssh"
  AUTH_KEYS="$SSH_DIR/authorized_keys"
  mkdir -p "$SSH_DIR"
  chmod 700 "$SSH_DIR"
  chown "$TARGET_USER:$TARGET_USER" "$SSH_DIR"
  if [[ ! -f "$SSH_DIR/id_ed25519" ]]; then
    sudo -u "$TARGET_USER" ssh-keygen -t ed25519 -N "" -f "$SSH_DIR/id_ed25519" -C "sentinel-x-$TARGET_USER"
  fi
  if [[ -f "$SSH_DIR/id_ed25519.pub" ]]; then
    touch "$AUTH_KEYS"
    if ! grep -qf "$SSH_DIR/id_ed25519.pub" "$AUTH_KEYS" 2>/dev/null; then
      cat "$SSH_DIR/id_ed25519.pub" >> "$AUTH_KEYS"
    fi
    chmod 600 "$AUTH_KEYS"
    chown "$TARGET_USER:$TARGET_USER" "$AUTH_KEYS"
  fi
  # Ne durcir que si au moins une clé autorisée
  if [[ -s "$AUTH_KEYS" ]]; then
    mkdir -p /etc/ssh/sshd_config.d
    cat > /etc/ssh/sshd_config.d/99-sentinel.conf << 'SSHD'
# SENTINEL-X — durcissement SSH
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
MaxAuthTries 3
SSHD
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    echo "[ssh] drop-in 99-sentinel.conf appliqué"
  else
    echo "[ssh] AUCUNE clé authorized_keys — PasswordAuthentication laissé intact (anti lock-out)"
  fi
else
  echo "[ssh] openssh-server non installé — surface d'attaque réduite (recommandé pour la démo)"
fi

# --- 4. sysctl ---
SYSCTL_FILE="/etc/sysctl.d/99-sentinel.conf"
cat > "$SYSCTL_FILE" << 'SYS'
# SENTINEL-X sysctl hardening
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
SYS
sysctl --system >/dev/null 2>&1 || sysctl -p "$SYSCTL_FILE" || true
echo "[sysctl] $SYSCTL_FILE appliqué"

# --- 5. fail2ban optionnel ---
if command -v fail2ban-client >/dev/null 2>&1 || dpkg -l fail2ban 2>/dev/null | grep -q '^ii'; then
  systemctl enable --now fail2ban 2>/dev/null || true
  echo "[fail2ban] activé"
else
  echo "[fail2ban] non installé (ok hors-ligne) — optionnel"
fi

# --- 6. Recommandation rootless docker ---
echo "[docker] rootless / userns-remap : non activé automatiquement (nécessite reconfig dockerd)."
echo "  Recommandation jury : tester 'dockerd-rootless-setuptool.sh install' hors démo."

echo "===== harden.sh terminé $(date -Is) ====="
