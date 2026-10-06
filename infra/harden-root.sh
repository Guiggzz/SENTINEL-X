#!/usr/bin/env bash
# =============================================================================
# SENTINEL-X — durcissement système (partie qui nécessite root)
# Usage :   sudo bash infra/harden-root.sh
#
# - Idempotent : peut être relancé autant de fois que voulu.
# - Anti lock-out : on ne fait JAMAIS "ufw reset" ; SSH 22 (clé uniquement) reste
#   autorisé, 443 (HTTPS) et 8883 (MQTTS) restent autorisés.
# - Ne touche PAS aux conteneurs healthai-* ni à leurs ports (3002, 3100, 9090).
# - Ne touche PAS au firmware, aux identifiants MQTT ni au certificat TLS.
# Journal : infra/hardening/harden-root.log
# =============================================================================
set -uo pipefail

TARGET_USER="${SUDO_USER:-guiggzz}"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$INFRA_DIR/hardening/harden-root.log"
# Sous-réseaux autorisés pour le MQTTS de l'ESP8266 : hotspot iPhone + AP de table (docs/reseau-table.md).
# Mettre MQTT_SUBNETS=any pour ouvrir à tous (authentification + TLS restent obligatoires côté Mosquitto).
MQTT_SUBNETS="${MQTT_SUBNETS:-172.20.10.0/28 192.168.10.0/24}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERREUR : lancer avec sudo :  sudo bash infra/harden-root.sh" >&2
  exit 1
fi
mkdir -p "$INFRA_DIR/hardening"
exec > >(tee -a "$LOG") 2>&1
echo "===== SENTINEL-X harden-root.sh $(date -Is) (utilisateur cible : $TARGET_USER) ====="

ok()   { echo "  [OK] $*"; }
info() { echo "  [..] $*"; }
warn() { echo "  [!!] $*"; }

# -----------------------------------------------------------------------------
# 1. Samba (139/445 + NetBIOS 137/138) : inutile pour SENTINEL-X, exposé au Wi-Fi
#    Réactivation éventuelle : sudo systemctl enable --now smbd nmbd
# -----------------------------------------------------------------------------
echo "[1] Samba"
for svc in smbd nmbd samba-ad-dc; do
  if systemctl list-unit-files "$svc.service" >/dev/null 2>&1 && systemctl cat "$svc.service" >/dev/null 2>&1; then
    systemctl disable --now "$svc.service" >/dev/null 2>&1 && ok "$svc arrêté et désactivé" || warn "$svc : échec arrêt"
  fi
done

# -----------------------------------------------------------------------------
# 2. Apache hôte sur :80 (page par défaut Ubuntu + bannière de version) -> arrêté.
#    Le port 80 est ensuite repris par Caddy (conteneur) qui ne fait QUE rediriger en HTTPS.
#    Réactivation éventuelle : rm infra/docker-compose.override.yml && sudo systemctl enable --now apache2
# -----------------------------------------------------------------------------
echo "[2] Apache -> redirection HTTPS par Caddy"
if systemctl cat apache2.service >/dev/null 2>&1; then
  systemctl disable --now apache2.service >/dev/null 2>&1 && ok "apache2 arrêté et désactivé" || warn "apache2 : échec arrêt"
fi
sleep 1
if ss -Htln 'sport = :80' | grep -q .; then
  warn "port 80 encore occupé (pas par Apache ?) — redirection Caddy non activée"
  ss -Htlnp 'sport = :80'
else
  install -o "$TARGET_USER" -g "$TARGET_USER" -m 0644 "$INFRA_DIR/docker-compose.http80.yml" "$INFRA_DIR/docker-compose.override.yml"
  # IMPORTANT : environnement propre (jamais un shell ayant sourcé infra/.env : le "$$" du hash casse le login)
  if runuser -u "$TARGET_USER" -- env -i HOME="/home/$TARGET_USER" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin USER="$TARGET_USER" \
       bash -c "cd '$INFRA_DIR' && docker compose up -d --no-deps proxy"; then
    sleep 3
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ || true)"
    [[ "$code" == "308" ]] && ok "http://  -> 308 vers https (Caddy)" || warn "redirection :80 inattendue (code $code)"
  else
    warn "docker compose a échoué : retrait de l'override pour ne pas casser la démo"
    rm -f "$INFRA_DIR/docker-compose.override.yml"
    runuser -u "$TARGET_USER" -- env -i HOME="/home/$TARGET_USER" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin USER="$TARGET_USER" \
       bash -c "cd '$INFRA_DIR' && docker compose up -d --no-deps proxy" || true
  fi
fi

# -----------------------------------------------------------------------------
# 3. UFW : deny par défaut en entrée, sans reset (règles existantes conservées)
# -----------------------------------------------------------------------------
echo "[3] UFW"
if ! command -v ufw >/dev/null 2>&1; then
  apt-get install -y ufw >/dev/null 2>&1 || warn "installation ufw impossible (hors-ligne ?)"
fi
if command -v ufw >/dev/null 2>&1; then
  ufw default deny incoming  >/dev/null
  ufw default allow outgoing >/dev/null
  ufw default deny routed    >/dev/null
  # SSH : autorisé (clé uniquement côté sshd) avec limitation anti brute-force (6 conn./30 s)
  ufw limit 22/tcp comment 'SSH cle uniquement (rate-limit)' >/dev/null
  ufw allow 443/tcp comment 'SENTINEL HTTPS' >/dev/null
  ufw allow 80/tcp  comment 'SENTINEL redirection HTTP->HTTPS (Caddy)' >/dev/null
  if [[ "$MQTT_SUBNETS" == "any" ]]; then
    ufw allow 8883/tcp comment 'SENTINEL MQTTS (TLS+auth)' >/dev/null
  else
    for net in $MQTT_SUBNETS; do
      ufw allow from "$net" to any port 8883 proto tcp comment 'SENTINEL MQTTS ESP' >/dev/null
    done
  fi
  # Ancienne règle MQTT en clair (1883) : plus utilisée depuis le passage en MQTTS
  ufw --force delete allow from 172.20.10.0/28 to any port 1883 proto tcp >/dev/null 2>&1 && ok "règle 1883 (MQTT clair) supprimée" || true
  # Refus explicites (documentaires : déjà couverts par deny par défaut)
  ufw allow from 172.16.0.0/12 to any port 8081 proto tcp comment 'SENTINEL vision depuis Docker uniquement' >/dev/null
  ufw deny 8081/tcp comment 'SENTINEL vision interdit depuis le LAN' >/dev/null
  ufw deny 3000/tcp comment 'SENTINEL API interne' >/dev/null
  ufw deny 5432/tcp comment 'PostgreSQL interne' >/dev/null
  ufw deny 139,445/tcp comment 'Samba interdit' >/dev/null
  ufw deny 137,138/udp comment 'NetBIOS interdit' >/dev/null
  ufw logging low >/dev/null
  ufw --force enable >/dev/null && ok "UFW actif"
  ufw status verbose
fi

# -----------------------------------------------------------------------------
# 4. Ports publiés par Docker : ils contournent UFW (DNAT -> FORWARD).
#    Filtrage dans DOCKER-USER : 8883 réservé au sous-réseau de l'ESP (v4), coupé en IPv6.
#    443/80 ouverts. Les autres ports Docker (healthai) ne sont PAS modifiés.
# -----------------------------------------------------------------------------
echo "[4] DOCKER-USER"
# Nettoyage idempotent de nos anciennes règles (commentaire SENTINEL8883*)
purge() { local bin="$1"; "$bin" -S DOCKER-USER 2>/dev/null | grep 'SENTINEL8883' | sed 's/^-A /-D /' | while read -r r; do eval "$bin $r"; done; }
if command -v iptables >/dev/null 2>&1; then
  iptables -nL DOCKER-USER >/dev/null 2>&1 || iptables -N DOCKER-USER
  purge iptables
  if [[ "$MQTT_SUBNETS" != "any" ]]; then
    iptables -I DOCKER-USER 1 -p tcp --dport 8883 -m conntrack --ctstate NEW -j DROP -m comment --comment SENTINEL8883
    for net in $MQTT_SUBNETS; do
      iptables -I DOCKER-USER 1 -p tcp --dport 8883 -s "$net" -j RETURN -m comment --comment SENTINEL8883
    done
    ok "IPv4 : 8883 accepté seulement depuis : $MQTT_SUBNETS"
  fi
fi
if command -v ip6tables >/dev/null 2>&1 && ip6tables -nL DOCKER-USER >/dev/null 2>&1; then
  purge ip6tables
  ip6tables -I DOCKER-USER 1 -p tcp --dport 8883 -m conntrack --ctstate NEW -j DROP -m comment --comment SENTINEL8883v6
  ok "IPv6 : 8883 bloqué (l'ESP8266 est IPv4 uniquement)"
fi
# Persistance au reboot : bloc SENTINEL-X de /etc/ufw/after.rules réécrit avec les mêmes sous-réseaux
AFTER=/etc/ufw/after.rules
B="# BEGIN SENTINEL-X DOCKER-USER"; E="# END SENTINEL-X DOCKER-USER"
if [[ -f "$AFTER" ]]; then
  cp -a "$AFTER" "$AFTER.sentinel.bak"
  tmp="$(mktemp)"
  awk -v b="$B" -v e="$E" '$0==b{skip=1;next} $0==e{skip=0;next} !skip{print}' "$AFTER" > "$tmp"
  {
    cat "$tmp"
    echo "$B"
    echo "*filter"
    echo ":DOCKER-USER - [0:0]"
    echo "-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN"
    if [[ "$MQTT_SUBNETS" != "any" ]]; then
      for net in $MQTT_SUBNETS; do echo "-A DOCKER-USER -p tcp --dport 8883 -s $net -j RETURN"; done
      echo "-A DOCKER-USER -p tcp --dport 8883 -j DROP"
    fi
    echo "# 443/80 ouverts ; autres ports Docker (healthai) non modifiés"
    echo "-A DOCKER-USER -j RETURN"
    echo "COMMIT"
    echo "$E"
  } > "$AFTER"
  rm -f "$tmp"
  ok "bloc DOCKER-USER persistant mis à jour dans $AFTER (sauvegarde : $AFTER.sentinel.bak)"
fi

# -----------------------------------------------------------------------------
# 5. SSH : clé uniquement (anti lock-out : seulement si une clé autorisée existe)
#    Fichier "00-" : sshd garde la PREMIÈRE valeur lue, il prime donc sur les autres drop-ins.
# -----------------------------------------------------------------------------
echo "[5] SSH"
AUTH_KEYS="/home/$TARGET_USER/.ssh/authorized_keys"
DROPIN=/etc/ssh/sshd_config.d/00-sentinel-hardening.conf
if command -v sshd >/dev/null 2>&1 || [[ -x /usr/sbin/sshd ]]; then
  if [[ -s "$AUTH_KEYS" ]]; then
    backup=""
    [[ -f "$DROPIN" ]] && backup="$(mktemp)" && cp "$DROPIN" "$backup"
    cat > "$DROPIN" <<'SSHD'
# SENTINEL-X — SSH durci (généré par infra/harden-root.sh)
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
MaxStartups 10:30:60
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
SSHD
    chmod 644 "$DROPIN"
    if /usr/sbin/sshd -t; then
      systemctl reload ssh.service 2>/dev/null || systemctl reload sshd.service 2>/dev/null || true
      ok "sshd : $(/usr/sbin/sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin|authenticationmethods) ' | tr '\n' ' ')"
    else
      warn "configuration sshd invalide : retour arrière"
      if [[ -n "$backup" ]]; then cp "$backup" "$DROPIN"; else rm -f "$DROPIN"; fi
    fi
    [[ -n "$backup" ]] && rm -f "$backup"
  else
    warn "aucune clé dans $AUTH_KEYS : configuration SSH laissée intacte (anti lock-out)"
  fi
else
  info "openssh-server absent"
fi

# -----------------------------------------------------------------------------
# 6. sysctl complémentaires (ip_forward NON modifié : requis par Docker)
# -----------------------------------------------------------------------------
echo "[6] sysctl"
cat > /etc/sysctl.d/99-sentinel-extra.conf <<'SYS'
# SENTINEL-X — durcissement réseau / noyau complémentaire
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
SYS
sysctl -q -p /etc/sysctl.d/99-sentinel-extra.conf && ok "sysctl appliqués"

# -----------------------------------------------------------------------------
# 7. Bilan
# -----------------------------------------------------------------------------
echo "[7] Ports en écoute (hors loopback)"
ss -Htln | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | sort -u
echo "===== harden-root.sh terminé $(date -Is) ====="
