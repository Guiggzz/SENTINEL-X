"""SENTINEL-X — génération du rapport d'ingénierie (HTML + CSS → PDF via WeasyPrint)."""
import os, subprocess, sys
from weasyprint import HTML

ROOT = os.path.expanduser("~/sentinel-x")
DOCS = f"{ROOT}/docs"
ASSETS = f"{DOCS}/assets"
GROUP = os.environ.get("SENTINEL_GROUP", "")          # ex. SENTINEL_GROUP=7 → M1-G7
GTAG = f"G{GROUP}" if GROUP else "G"
GLABEL = f"M1-G{GROUP}" if GROUP else "M1-G__"
OUT = f"{DOCS}/Workshop2026-M1-{GTAG}-Dossier.pdf"

subprocess.run([sys.executable, f"{ASSETS}/build/diagrams.py"], check=True)

CSS = r"""
@page {
  size: A4; margin: 20mm 17mm 18mm 17mm; background: #f2f1ec;
  @top-left { content: "SENTINEL-X · Rapport d'ingénierie technique"; font: 400 7.5pt "DejaVu Sans Mono"; color: #6b6a64;
              border-bottom: 0.6pt solid #d4d2c8; vertical-align: bottom; padding-bottom: 2mm; }
  @top-right { content: "Workshop2026-""" + GLABEL + r""""; font: 400 7.5pt "DejaVu Sans Mono"; color: #6b6a64;
               border-bottom: 0.6pt solid #d4d2c8; vertical-align: bottom; padding-bottom: 2mm; }
  @bottom-left { content: "AetherCorp · EPSI Workshop Bac+4 · octobre 2026"; font: 400 7.5pt "DejaVu Sans Mono"; color: #6b6a64; }
  @bottom-right { content: counter(page) " / " counter(pages); font: 400 7.5pt "DejaVu Sans Mono"; color: #111; }
}
@page cover { margin: 0; @top-left { content: none; } @top-right { content: none; }
              @bottom-left { content: none; } @bottom-right { content: none; } }
@page poster { size: A3 landscape; margin: 12mm 14mm;
               @top-left { content: none; border: none; } @top-right { content: none; border: none; }
               @bottom-left { content: none; }
               @bottom-right { content: none; } }

:root { --bg:#f2f1ec; --ink:#111; --muted:#6b6a64; --rule:#d4d2c8; --rule-s:#b8b6ab; --surface:#eae9e2; --accent:#c45c26; --ok:#2f7d4a; }
* { box-sizing: border-box; }
html { font-family: "Ubuntu Sans", "Ubuntu", "Helvetica Neue", Arial, sans-serif; color: var(--ink); font-size: 9.6pt; line-height: 1.45; }
body { margin: 0; }
.mono, code, td.m, .k { font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; }
code { font-size: 0.86em; background: var(--surface); padding: 0 2px; }
h1 { font-size: 15pt; font-weight: 600; letter-spacing: 0.02em; margin: 0 0 2mm; padding-bottom: 2mm; border-bottom: 0.8pt solid var(--ink);
     string-set: chap content(); page-break-before: always; }
h1 .n { font-family: "DejaVu Sans Mono"; color: var(--accent); font-weight: 400; margin-right: 3mm; }
h2 { font-size: 10.5pt; font-weight: 600; margin: 5mm 0 2mm; page-break-after: avoid; }
h3 { font-size: 9pt; font-weight: 600; margin: 4mm 0 1.5mm; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
p { margin: 0 0 2.4mm; text-align: left; hyphens: auto; }
.lead { color: var(--muted); font-size: 9.2pt; margin-bottom: 4mm; }
ul { margin: 0 0 3mm; padding-left: 4.5mm; } li { margin-bottom: 0.8mm; }
table { width: 100%; border-collapse: collapse; margin: 1.5mm 0 4mm; font-size: 8.3pt; page-break-inside: auto; }
th { text-align: left; font-weight: 400; color: var(--muted); font-size: 7.6pt; border-bottom: 0.8pt solid var(--ink); padding: 1.4mm 2mm 1.2mm 0; }
td { border-bottom: 0.6pt solid var(--rule); padding: 1.4mm 2mm 1.4mm 0; vertical-align: top; }
tr { page-break-inside: avoid; }
td.m { font-size: 7.6pt; }
figure { margin: 2mm 0 4mm; page-break-inside: avoid; }
figure img { width: 100%; } figure.n90 { text-align: center; } figure.n90 img { width: 84%; } figure.n78 { text-align: center; } figure.n78 img { width: 76%; } figure.n78 figcaption { text-align: left; } figure.n90 figcaption { text-align: left; } figure img { border: 0.6pt solid var(--rule); }
figcaption { font-size: 7.6pt; color: var(--muted); font-family: "DejaVu Sans Mono"; margin-top: 1.2mm; }
.strip { display: flex; border-top: 0.8pt solid var(--ink); border-bottom: 0.6pt solid var(--rule); margin: 3mm 0 5mm; }
.strip .s { flex: 1; padding: 2mm 3mm 2.4mm; border-right: 0.6pt solid var(--rule); }
.strip .s:last-child { border-right: none; }
.strip .l { display: block; font-size: 7pt; color: var(--muted); margin-bottom: 1mm; }
.strip .v { display: block; font-family: "DejaVu Sans Mono"; font-size: 12.5pt; font-weight: 500; }
.strip .v.a { color: var(--accent); }
.tag { font-family: "DejaVu Sans Mono"; font-size: 7pt; letter-spacing: 0.03em; text-transform: uppercase; white-space: nowrap; }
.tag::before { content: ""; display: inline-block; width: 5pt; height: 5pt; border-radius: 50%; margin-right: 4pt; background: var(--rule-s); vertical-align: 0.5pt; }
.tag.ok::before { background: var(--ok); } .tag.part::before { background: var(--rule-s); border: 0.8pt solid var(--accent); }
.tag.ko { color: var(--accent); } .tag.ko::before { background: var(--accent); }
.note { border-left: 1.6pt solid var(--accent); padding: 1.6mm 3mm; margin: 2mm 0 4mm; font-size: 8.6pt; background: transparent; }
.note.plain { border-left-color: var(--rule-s); color: var(--muted); }
.blank { display: inline-block; min-width: 38mm; border-bottom: 0.6pt solid var(--ink); height: 4.2mm; }
.fill { border: 0.6pt dashed var(--rule-s); padding: 3mm; margin: 1.5mm 0 3mm; min-height: 15mm; color: var(--muted); font-size: 8.2pt; }
.fill.tall { min-height: 24mm; }
.cols { display: flex; gap: 6mm; } .cols > div { flex: 1; }
pre { font-family: "DejaVu Sans Mono"; font-size: 7.2pt; background: var(--surface); border: 0.6pt solid var(--rule); padding: 2mm 3mm; white-space: pre-wrap; margin: 1mm 0 4mm; page-break-inside: avoid; }
.toc { margin-top: 2mm; } .toc div { display: flex; border-bottom: 0.6pt solid var(--rule); padding: 1.6mm 0; }
.toc .n { width: 10mm; font-family: "DejaVu Sans Mono"; color: var(--accent); } .toc .t { flex: 1; }
.toc .p::after { content: target-counter(attr(data-href), page); font-family: "DejaVu Sans Mono"; }
.check { list-style: none; padding-left: 0; } .check li::before { content: "☐  "; font-family: "DejaVu Sans"; color: var(--muted); }

/* ---------- couverture ---------- */
.cover { page: cover; height: 297mm; width: 210mm; padding: 16mm 17mm 14mm; position: relative; background: var(--bg); }
.cover .top { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 0.6pt solid var(--rule); padding-bottom: 3mm; }
.cover .brand { font-size: 12pt; font-weight: 600; letter-spacing: 0.06em; }
.cover .brand small { display: block; font-size: 8pt; font-weight: 400; color: var(--muted); letter-spacing: 0; margin-top: 0.6mm; }
.cover .badge { font-family: "DejaVu Sans Mono"; font-size: 8pt; color: var(--ink); }
.cover .badge::before { content: ""; display: inline-block; width: 6pt; height: 6pt; border-radius: 50%; background: var(--ok); margin-right: 5pt; }
.cover .kick { margin-top: 48mm; font-family: "DejaVu Sans Mono"; font-size: 8.5pt; color: var(--accent); letter-spacing: 0.08em; }
.cover .title { font-size: 34pt; font-weight: 600; letter-spacing: 0.03em; line-height: 1.05; margin: 3mm 0 3mm; }
.cover .subtitle { font-size: 13pt; color: var(--muted); font-weight: 400; margin-bottom: 12mm; }
.cover table.meta { width: 100%; font-size: 9pt; } .cover table.meta td { padding: 2mm 0; }
.cover table.meta td.k { width: 46mm; color: var(--muted); font-size: 8pt; }
.cover .team { margin-top: 9mm; }
.cover .foot { position: absolute; left: 17mm; right: 17mm; bottom: 14mm; border-top: 0.6pt solid var(--rule); padding-top: 3mm;
               display: flex; justify-content: space-between; font-family: "DejaVu Sans Mono"; font-size: 7.5pt; color: var(--muted); }
.cover .accentbar { position: absolute; left: 17mm; top: 70mm; width: 22mm; height: 1.4mm; background: var(--accent); }

/* ---------- poster A3 ---------- */
.poster { page: poster; page-break-before: always; }
.poster .ph { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1pt solid var(--ink); padding-bottom: 3mm; }
.poster .ph .t { font-size: 30pt; font-weight: 600; letter-spacing: 0.04em; line-height: 1; }
.poster .ph .t span { color: var(--accent); }
.poster .ph .sub { font-size: 11pt; color: var(--muted); margin-top: 2mm; }
.poster .ph .r { text-align: right; font-family: "DejaVu Sans Mono"; font-size: 9pt; color: var(--muted); line-height: 1.6; }
.poster .kpi { display: flex; border-bottom: 0.6pt solid var(--rule); }
.poster .kpi .s { flex: 1; padding: 3mm 4mm 3.5mm; border-right: 0.6pt solid var(--rule); }
.poster .kpi .s:last-child { border-right: none; }
.poster .kpi .l { font-size: 9pt; color: var(--muted); display: block; }
.poster .kpi .v { display: block; font-family: "DejaVu Sans Mono"; font-size: 22pt; font-weight: 500; line-height: 1.15; }
.poster .kpi .v.a { color: var(--accent); }
.poster .kpi .d { display: block; font-size: 7.5pt; color: var(--muted); font-family: "DejaVu Sans Mono"; }
.poster .grid { display: table; width: 100%; table-layout: fixed; border-collapse: collapse; }
.poster .grid > div { display: table-cell; vertical-align: top; }
.poster .grid > div { padding: 4mm 5mm 0; border-right: 0.6pt solid var(--rule); }
.poster .grid > div:last-child { border-right: none; padding-right: 0; }
.poster .grid > div:first-child { padding-left: 0; }
.poster h4 { font-size: 9pt; font-weight: 400; color: var(--muted); margin: 0 0 2mm; text-transform: uppercase; letter-spacing: 0.06em; }
.poster p, .poster li { font-size: 10.4pt; } .poster table { font-size: 9.6pt; } .poster h4 { font-size: 10pt; }
.poster .chain { display: flex; border-top: 1pt solid var(--ink); margin-top: 4mm; }
.poster .chain div { flex: 1; padding: 3mm 4mm; border-right: 0.6pt solid var(--rule); font-size: 9.6pt; }
.poster .chain div:last-child { border-right: none; }
.poster .chain b { display: block; font-family: "DejaVu Sans Mono"; color: var(--accent); font-weight: 400; font-size: 9pt; margin-bottom: 1mm; }
.poster img { width: 100%; border: 0.6pt solid var(--rule); }
.poster .tagline { font-size: 12pt; font-weight: 600; margin-top: 3mm; } .poster .tagline span { color: var(--accent); }
"""

def tag(kind, txt):
    return f'<span class="tag {kind}">{txt}</span>'

OK, PART, KO = "ok", "part", "ko"
team_rows = "".join(
    f"<tr><td>{n}</td><td>{f}</td><td>{r}</td></tr>" for n, f, r in [
        ("Nathanaël Lejuste", "DEV", "API"),
        ("Rémy Eroes", "DEV", "Dashboard &amp; système embarqué"),
        ("Amaury Malisova", "DEV", "IA"),
        ("Guillaume Breon", "DEV", "Dashboard &amp; système embarqué"),
    ])

HTMLDOC = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>SENTINEL-X — Rapport d'ingénierie technique</title>
<style>{CSS}</style></head><body>

<!-- ======================= COUVERTURE ======================= -->
<section class="cover">
  <div class="top">
    <div class="brand">SENTINEL-X<small>AetherCorp · Centre de surveillance de table</small></div>
    <div class="badge">Mission SENTINEL-X</div>
  </div>
  <div class="accentbar"></div>
  <div class="kick">RAPPORT D'INGÉNIERIE TECHNIQUE</div>
  <div class="title">Mission<br>SENTINEL-X</div>
  <div class="subtitle">Nœud IoT ESP8266, stack Docker sécurisée, IA prédictive et vision embarquée</div>
  <table class="meta">
    <tr><td class="k">Cadre</td><td>EPSI — Workshop Bac+4 (M1), octobre 2026</td></tr>
    <tr><td class="k">Option technique</td><td>Option B — topologie « Edge-to-Server » : le PC d'un apprenant joue le rôle de serveur local</td></tr>
    <tr><td class="k">Groupe</td><td class="mono">{GLABEL}</td></tr>
    <tr><td class="k">Version du document</td><td>5 octobre 2026 — dépôt prévu jeudi 8 octobre 2026</td></tr>
    <tr><td class="k">Fichier</td><td class="mono">Workshop2026-{GLABEL}-Dossier.pdf</td></tr>
  </table>
  <div class="team">
    <h3>Équipe (consortium)</h3>
    <table><tr><th>Membre</th><th>Filière (DEV / IA / INFRA / CYBER)</th><th>Rôle principal</th></tr>{team_rows}</table>
  </div>
</section>

<!-- ======================= SOMMAIRE ======================= -->
<h1 id="s0"><span class="n">00</span>Sommaire et synthèse</h1>
<div class="toc">
  <div><span class="n">01</span><span class="t">Architecture et schéma réseau</span><span class="p" data-href="#s1"></span></div>
  <div><span class="n">02</span><span class="t">Schéma de câblage électronique ESP8266</span><span class="p" data-href="#s2"></span></div>
  <div><span class="n">03</span><span class="t">Matrice de sécurité — TLS, hardening, preuves</span><span class="p" data-href="#s3"></span></div>
  <div><span class="n">04</span><span class="t">Documentation de l'IA — maintenance prédictive et vision</span><span class="p" data-href="#s4"></span></div>
  <div><span class="n">A3</span><span class="t">Annexe — poster A3 paysage</span><span class="p" data-href="#a3"></span></div>
</div>

<h2>Synthèse</h2>
<p>SENTINEL-X est un module de surveillance de table pour les centrales AetherCorp. Un nœud ESP8266 mesure la température,
l'humidité, le gaz/fumée et la présence, affiche son état sur un écran OLED et pilote un buzzer et deux LED. Il publie ses mesures
en MQTT chiffré (MQTTS) vers une stack Docker hébergée sur le PC portable d'un membre de l'équipe (Option B). Cette stack stocke les
données dans PostgreSQL, les expose sur un tableau de bord HTTPS temps réel, et alimente un service d'IA qui détecte les dérives
anormales des capteurs sans seuil statique. Une webcam USB reliée au PC alimente un service de vision YOLOv8n qui détecte les
personnes et déclenche l'alarme intrusion lorsqu'elle est armée.</p>

<div class="strip">
  <div class="s"><span class="l">Conteneurs Docker</span><span class="v">5</span></div>
  <div class="s"><span class="l">MQTT broker</span><span class="v">TLS 1.3</span></div>
  <div class="s"><span class="l">Inférence YOLO / trame</span><span class="v">≈ 49 ms</span></div>
  <div class="s"><span class="l">Rappel spray butane</span><span class="v a">20/20</span></div>
  <div class="s"><span class="l">Faux incidents (holdout)</span><span class="v">0</span></div>
</div>

<table>
  <tr><th>Brique</th><th>Technologie</th><th>Emplacement</th></tr>
  <tr><td>Nœud capteurs</td><td>ESP8266 NodeMCU v3, Arduino C++, PubSubClient + BearSSL</td><td class="m">firmware/sentinel_node/</td></tr>
  <tr><td>Broker</td><td>Eclipse Mosquitto 2.0.20, listener unique 8883 (TLS, ACL, anonyme refusé)</td><td class="m">infra/mosquitto/</td></tr>
  <tr><td>API + dashboard</td><td>FastAPI, WebSocket <code>/ws</code>, session opérateur, Bearer pour les clients machine</td><td class="m">backend/</td></tr>
  <tr><td>Base de données</td><td>PostgreSQL 16 (volume nommé, aucun port publié)</td><td class="m">infra/docker-compose.yml</td></tr>
  <tr><td>IA prédictive</td><td>Isolation Forest (scikit-learn), 8 features cinétiques</td><td class="m">ml/</td></tr>
  <tr><td>Vision</td><td>Ultralytics YOLOv8n sur CPU, flux MJPEG, service systemd utilisateur</td><td class="m">vision/</td></tr>
  <tr><td>Reverse proxy</td><td>Caddy 2.8, HTTPS 443, en-têtes de sécurité (HSTS, CSP, X-Frame-Options)</td><td class="m">infra/caddy/Caddyfile</td></tr>
</table>
<p class="lead">Toutes les valeurs chiffrées de ce rapport proviennent du dépôt (README, <code>ml/evaluation.md</code>, fichiers d'infrastructure,
commentaires du firmware) ou de mesures relevées sur la machine le 5 octobre 2026.</p>

<!-- ======================= 01 RÉSEAU ======================= -->
<h1 id="s1"><span class="n">01</span>Architecture et schéma réseau</h1>
<p class="lead">Option B : le PC apprenant (Ubuntu, interface Wi-Fi <code>wlp0s20f3</code>) héberge toute la stack. Le boîtier ne contient que
l'ESP8266 et ses capteurs. La webcam est branchée en USB sur le PC.</p>
<figure><img src="file://{ASSETS}/schema-reseau.png"><figcaption>Fig. 1 — Topologie de table et flux. Accent : flux chiffrés entrants autorisés par le pare-feu.</figcaption></figure>

<h2>Plan d'adressage</h2>
<table>
  <tr><th>Élément</th><th>Actuel (hotspot iPhone)</th><th>Cible sujet (point d'accès dédié) — proposition</th></tr>
  <tr><td>Sous-réseau</td><td class="m">172.20.10.0/28 (14 hôtes utiles)</td><td class="m">192.168.10.0/24</td></tr>
  <tr><td>Passerelle / AP</td><td class="m">172.20.10.1 (iPhone)</td><td class="m">192.168.10.1 (AP de table)</td></tr>
  <tr><td>PC serveur</td><td class="m">172.20.10.4 (DHCP)</td><td class="m">adresse fixe ou réservation DHCP</td></tr>
  <tr><td>ESP8266 sentinel-node-01</td><td class="m">172.20.10.2 (observé dans les journaux Mosquitto)</td><td class="m">réservation DHCP</td></tr>
  <tr><td>Règle MQTTS</td><td class="m">8883 autorisé depuis 172.20.10.0/28 uniquement</td><td class="m">même règle, source 192.168.10.0/24</td></tr>
</table>
<p>Le /28 du hotspot limite naturellement la table à 14 hôtes et isole l'équipe des réseaux des autres groupes. Le passage
sur le point d'accès dédié demandé par le sujet ne modifie que la variable <code>HOTSPOT_SUBNET</code> de
<code>infra/hardening/harden.sh</code> et l'hôte MQTT du firmware.</p>

<h2>Matrice des flux</h2>
<table>
  <tr><th>Source → destination</th><th>Port / protocole</th><th>Exposition</th><th>Protection</th></tr>
  <tr><td>ESP8266 → sentinel-mosquitto</td><td class="m">8883 MQTTS</td><td>LAN, sous-réseau de table</td><td>TLS, pin SHA1 du certificat serveur, utilisateur dédié + ACL</td></tr>
  <tr><td>Navigateur → sentinel-proxy</td><td class="m">443 HTTPS</td><td>LAN</td><td>TLS (CA locale), session HttpOnly/Secure/SameSite=Strict</td></tr>
  <tr><td>sentinel-proxy → sentinel-api</td><td class="m">3000 HTTP</td><td>réseau Docker</td><td>publié seulement sur 127.0.0.1, UFW deny 3000</td></tr>
  <tr><td>sentinel-api → sentinel-vision</td><td class="m">8081 HTTP</td><td>hôte (host.docker.internal)</td><td>UFW : autorisé depuis 172.16.0.0/12, refusé ailleurs ; accès client via <code>/cam</code> authentifié</td></tr>
  <tr><td>api / ml → sentinel-db</td><td class="m">5432</td><td>réseau <code>sentinel-back</code> (internal)</td><td>aucun port hôte</td></tr>
  <tr><td>api / ml → sentinel-mosquitto</td><td class="m">8883 MQTTS</td><td>réseau Docker</td><td>TLS + comptes séparés (<code>sentinel-ml</code> écrit <code>sentinel/ai/#</code> et <code>sentinel/+/cmd</code>)</td></tr>
</table>

<h2>Topics MQTT</h2>
<table>
  <tr><th>Topic</th><th>Sens</th><th>Contenu</th></tr>
  <tr><td class="m">sentinel/{{device_id}}/telemetry</td><td>ESP → broker</td><td>JSON capteurs (température, humidité, gaz, présence, actionneurs, RSSI)</td></tr>
  <tr><td class="m">sentinel/{{device_id}}/alerts</td><td>ESP → broker</td><td>événements présence / actionneurs</td></tr>
  <tr><td class="m">sentinel/{{device_id}}/status</td><td>ESP → broker (retained)</td><td><code>online</code> / <code>offline</code></td></tr>
  <tr><td class="m">sentinel/{{device_id}}/cmd</td><td>API, ML → ESP</td><td>buzzer (songs <code>gaz</code>, <code>intrus</code>…), LED, mode auto</td></tr>
  <tr><td class="m">sentinel/ai/&lt;device&gt;/risk · ai/alerts · ai/status</td><td>ML → broker</td><td>risque 0-100, incidents IA, LWT du service</td></tr>
</table>

<h2>Services Docker</h2>
<table>
  <tr><th>Conteneur</th><th>Image</th><th>Ports hôte</th><th>Réseaux</th><th>Limites</th></tr>
  <tr><td class="m">sentinel-mosquitto</td><td class="m">eclipse-mosquitto:2.0.20</td><td class="m">8883</td><td class="m">front, back</td><td class="m">128 Mo · 0,5 CPU · 100 PID</td></tr>
  <tr><td class="m">sentinel-db</td><td class="m">postgres:16-alpine</td><td class="m">aucun</td><td class="m">back</td><td class="m">512 Mo · 1 CPU · 200 PID</td></tr>
  <tr><td class="m">sentinel-api</td><td class="m">build backend/</td><td class="m">127.0.0.1:3000</td><td class="m">front, back</td><td class="m">512 Mo · 1 CPU · 200 PID</td></tr>
  <tr><td class="m">sentinel-ml</td><td class="m">build ml/</td><td class="m">aucun</td><td class="m">front, back</td><td class="m">768 Mo · 1 CPU · 200 PID</td></tr>
  <tr><td class="m">sentinel-proxy</td><td class="m">caddy:2.8-alpine</td><td class="m">443</td><td class="m">front</td><td class="m">128 Mo · 0,5 CPU · 100 PID</td></tr>
</table>
<p>Journaux Docker en rotation (<code>json-file</code>, 10 Mo × 3 fichiers par conteneur) pour éviter la saturation du disque sous
l'afflux de messages MQTT. Le bloc MCO du dashboard (<code>/api/v1/system</code>) suit CPU, RAM et disque de l'hôte, le débit MQTT et
l'état des sondes mosquitto / db / ml / nœud.</p>

<!-- ======================= 02 CÂBLAGE ======================= -->
<h1 id="s2"><span class="n">02</span>Schéma de câblage électronique ESP8266</h1>
<p class="lead">Carte NodeMCU v3 alimentée en USB. Affectation des broches relevée dans
<code>firmware/sentinel_node/sentinel_node.ino</code> : « DHT22 D5 | PIR D6 | MQ-2 A0 | OLED SDA D2 / SCL D1 | Buzzer D7 | LED rouge D0 | LED verte D8 ».</p>
<figure class="n90"><img src="file://{ASSETS}/schema-cablage.png"><figcaption>Fig. 2 — Câblage du nœud sentinel-node-01.</figcaption></figure>

<table>
  <tr><th>Composant</th><th>Broche ESP</th><th>Alimentation</th><th>Remarque</th></tr>
  <tr><td>DHT22 (température / humidité)</td><td class="m">D5 (GPIO14)</td><td class="m">3V3 · GND</td><td>lecture toutes les 2 s (limite du capteur)</td></tr>
  <tr><td>OLED SSD1306 0,96" I2C</td><td class="m">D1 SCL · D2 SDA</td><td class="m">3V3 · GND</td><td>adresse 0x3C, <code>Wire.begin(D2, D1)</code> ; affiche IP, état MQTTS, alertes</td></tr>
  <tr><td>PIR HW-416A</td><td class="m">D6 (GPIO12)</td><td class="m">5 V (VU) · GND</td><td>sortie logique 3,3 V ; anti-rebond logiciel</td></tr>
  <tr><td>MQ-2 gaz / fumée</td><td class="m">A0 (sortie AO)</td><td class="m">5 V (VU) · GND</td><td>chauffe nécessaire après mise sous tension</td></tr>
  <tr><td>Buzzer piézo</td><td class="m">D7 (GPIO13)</td><td class="m">— · GND</td><td>sirènes <code>gaz</code> et <code>intrus</code> (<code>alarms.h</code>)</td></tr>
  <tr><td>LED rouge</td><td class="m">D0 (GPIO16)</td><td class="m">via R 220–330 Ω → GND</td><td>présence, alarme ; clignote si déconnecté</td></tr>
  <tr><td>LED verte</td><td class="m">D8 (GPIO15)</td><td class="m">via R 220–330 Ω → GND</td><td>connecté, RAS</td></tr>
</table>

<h2>Points d'attention</h2>
<ul>
  <li><b>D8 / GPIO15</b> doit être au niveau bas au démarrage : la LED et sa résistance vers GND jouent le rôle de pull-down, ne pas y brancher de pull-up.</li>
  <li><b>A0</b> : l'entrée analogique de la NodeMCU accepte 0–3,3 V grâce à son pont diviseur. Le MQ-2 étant alimenté en 5 V, vérifier que la tension AO reste dans cette plage (un pont diviseur externe sinon).</li>
  <li><b>Masse commune</b> à tous les modules ; le 5 V est pris sur VU (USB). <b>Cadence</b> : le firmware actuel publie la télémétrie toutes les 0,5 s (<code>TELEMETRY_MS</code>), le DHT22 n'est relu que toutes les 2 s ; le modèle IA raisonne sur une période nominale de 2 s.</li>
  <li><b>PIR</b> : jugé instable sur table, il reste publié dans la télémétrie mais l'alarme intrusion s'appuie sur la vision YOLO.</li>
</ul>

<!-- ======================= 03 SÉCURITÉ ======================= -->
<h1 id="s3"><span class="n">03</span>Matrice de sécurité</h1>
<p class="lead">Source : <code>docs/matrice-securite.md</code>, preuves issues de <code>infra/hardening/verify.sh</code> (<code>docs/preuves-securite.txt</code>, exécution du 5 octobre 2026, 14:07)
et contrôles complémentaires du 5 octobre 2026 vers 15:00.</p>

<table>
  <tr><th style="width:15%">Menace</th><th style="width:17%">Vecteur (jury / pentest)</th><th style="width:16%">Impact</th><th>Mitigation implémentée</th><th style="width:20%">Risque résiduel</th></tr>
  <tr><td>MitM / sniffing MQTT</td><td>Wireshark sur le hotspot</td><td>Lecture télémétrie, injection de commandes</td><td>MQTTS (port 8883, TLS 1.2 minimum, négocié en TLS 1.3), certificat avec SAN, authentification + ACL ; ESP : pin SHA1 du certificat (<code>MQTT_CERT_FINGERPRINT</code>)</td><td>CA auto-signée à importer dans les navigateurs</td></tr>
  <tr><td>Injection de payload MQTT</td><td>Client rogue / Metasploit</td><td>Commandes ESP, fausses alertes</td><td><code>allow_anonymous false</code>, ACL par rôle, mots de passe forts générés</td><td>Compromission d'un secret MQTT</td></tr>
  <tr><td>DoS broker</td><td>Flood connexions / messages</td><td>Perte de la supervision</td><td><code>max_connections 50</code>, <code>max_inflight_messages 20</code>, <code>max_queued_messages 100</code>, <code>max_packet_size 8192</code>, rotation des logs, limites CPU/RAM</td><td>DoS L2/L3 (Wi-Fi) hors MQTT</td></tr>
  <tr><td>Brute force API</td><td>POST login / commandes</td><td>Prise de contrôle du dashboard</td><td>Session HttpOnly + Secure + SameSite=Strict, rate-limit login (8/min/IP) et commandes (30/min/IP), Bearer <code>API_TOKEN</code> pour les clients machine</td><td>Mot de passe opérateur faible s'il n'est pas changé</td></tr>
  <tr><td>Exposition de services</td><td>Nmap</td><td>Surface d'attaque</td><td>UFW (deny par défaut) + chaîne DOCKER-USER ; 1883, 3000 et 8081 non exposés au LAN ; HTTPS seul pour l'interface</td><td>Retour d'un bind 0.0.0.0</td></tr>
  <tr><td>Accès physique ESP</td><td>Vol, dump flash</td><td>Secrets Wi-Fi / MQTT</td><td><code>secrets.h</code> hors git, utilisateur MQTT dédié restreint par ACL</td><td>Flash lisible avec accès physique</td></tr>
  <tr><td>Évasion de conteneur</td><td>CVE / mauvaise config Docker</td><td>Accès hôte</td><td>non-root, <code>read_only</code>, <code>cap_drop ALL</code>, <code>no-new-privileges</code>, limites de ressources</td><td>Docker rootful</td></tr>
  <tr><td>Client MQTT illégitime</td><td>Connexion avec un mauvais compte</td><td>Lecture / écriture hors périmètre</td><td>ACL stricte par utilisateur</td><td>Fuite du fichier <code>.env</code></td></tr>
  <tr><td>Accès caméra</td><td>URL directe du flux</td><td>Fuite d'images</td><td>Flux servi par l'API sur <code>/cam/*</code> derrière authentification ; port 8081 filtré</td><td>—</td></tr>
</table>

<h2>État du durcissement</h2>
<table>
  <tr><th style="width:30%">Mesure</th><th style="width:14%">État</th><th>Preuve / commentaire</th></tr>
  <tr><td>MQTTS 8883, port 1883 fermé</td><td>{tag(OK,'Fait')}</td><td><code>openssl s_client</code> : TLSv1.3, TLS_AES_256_GCM_SHA384 ; MQTT anonyme refusé ; 1883 non joignable ; seul <code>listener 8883</code> dans mosquitto.conf</td></tr>
  <tr><td>ESP en MQTTS avec pin du certificat</td><td>{tag(OK,'Fait')}</td><td><code>setFingerprint(MQTT_CERT_FINGERPRINT)</code>, buffers TLS 2048/512 ; connexions <code>sentinel-node-01</code> depuis 172.20.10.2 visibles côté broker. Choix du pin SHA1 : fonctionne hors ligne sans NTP.</td></tr>
  <tr><td>HTTPS 443 (Caddy)</td><td>{tag(OK,'Fait')}</td><td>TLSv1.3 ; HSTS, CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, en-tête Server retiré</td></tr>
  <tr><td>Authentification dashboard / API</td><td>{tag(OK,'Fait')}</td><td>compte <code>operateur</code> (mot de passe hors git), cookie de session, WebSocket refusé sans session (code 4401)</td></tr>
  <tr><td>Caméra derrière authentification</td><td>{tag(OK,'Fait')}</td><td><code>GET https://127.0.0.1/cam/health</code> sans session → <b>401</b> (contrôle du 5 octobre 2026)</td></tr>
  <tr><td>UFW</td><td>{tag(OK,'Actif')}</td><td>deny incoming par défaut ; allow 443, 80 (Apache hôte) ; 8883 depuis 172.20.10.0/28 ; deny 3000 ; 8081 depuis 172.16.0.0/12 puis deny</td></tr>
  <tr><td>DOCKER-USER</td><td>{tag(OK,'Fait')}</td><td>bloc SENTINEL-X dans <code>/etc/ufw/after.rules</code> + règle iptables immédiate : 8883 abandonné hors sous-réseau de table</td></tr>
  <tr><td>Conteneurs durcis</td><td>{tag(PART,'Partiel')}</td><td>api, ml, proxy : read-only, cap_drop ALL (proxy : NET_BIND_SERVICE seul), api/ml non-root. mosquitto et db : no-new-privileges + limites, mais ni read-only ni cap_drop (entrypoints officiels)</td></tr>
  <tr><td>SSH par clé uniquement</td><td>{tag(PART,'Préparé')}</td><td>openssh-server non installé : aucun port 22 exposé. <code>harden.sh</code> applique la configuration clé ed25519 seule (<code>99-sentinel.conf</code>) si le service est installé.</td></tr>
  <tr><td>sysctl réseau</td><td>{tag(OK,'Fait')}</td><td><code>/etc/sysctl.d/99-sentinel.conf</code> appliqué</td></tr>
  <tr><td>Docker rootless / userns-remap</td><td>{tag(KO,'Non fait')}</td><td>recommandation, nécessite de reconfigurer dockerd hors démo</td></tr>
  <tr><td>fail2ban, nmap de contrôle</td><td>{tag(KO,'Non fait')}</td><td>non installés ; nmap à prévoir pour la vérification avant pentest</td></tr>
</table>

<div class="note"><b>À traiter avant jeudi.</b> Docker publie des ports en contournant UFW. Sur l'hôte, d'autres conteneurs hors projet
publient <span class="mono">3002</span>, <span class="mono">3100</span> et <span class="mono">9090</span> sur 0.0.0.0, et la chaîne DOCKER-USER actuelle les laisse passer.
Un Nmap adverse les verra. Il faut les filtrer dans DOCKER-USER ou arrêter ces services pendant le pentest. Les ports 139/445 (Samba hôte) sont bloqués par la politique UFW par défaut.</div>

<h2>Extraits de preuves</h2>
<pre>### openssl s_client MQTTS :8883
subject=C = FR, O = SENTINEL-X, OU = EPSI-M1, CN = sentinel.local
issuer=C = FR, O = SENTINEL-X, OU = EPSI-M1, CN = SENTINEL-X Local CA
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384

### MQTT anonyme refusé (8883)     OK: anonyme refusé (ou TLS requis)
### Port 1883 fermé sur l'hôte     OK: 1883 fermé / non joignable

### UFW status
Default: deny (incoming), allow (outgoing), deny (routed)
443/tcp    ALLOW IN  Anywhere          # SENTINEL HTTPS
3000/tcp   DENY IN   Anywhere          # SENTINEL api deny
8883/tcp   ALLOW IN  172.20.10.0/28    # SENTINEL MQTTS hotspot
8081/tcp   ALLOW IN  172.16.0.0/12     # SENTINEL vision docker
8081/tcp   DENY IN   Anywhere          # SENTINEL vision deny others</pre>
<p>Preuve Wireshark à réaliser sur le hotspot pendant la démo : filtre <code>tcp.port==8883</code>, charge utile illisible (TLS).</p>

<!-- ======================= 04 IA ======================= -->
<h1 id="s4"><span class="n">04</span>Documentation de l'IA</h1>
<h2>4.1 Maintenance prédictive — Isolation Forest</h2>
<p>Le service <code>sentinel-ml</code> ne contient aucune règle du type <code>if temp &gt; 40</code>. Un modèle <b>Isolation Forest</b>
(scikit-learn, 300 arbres, contamination 0,005) apprend la dynamique normale des capteurs et signale les anomalies cinétiques :
dérives, montées rapides, corrélations suspectes. Les seuils de décision sont <b>appris</b> à partir des quantiles du score sur
l'historique normal, et non fixés sur une valeur capteur.</p>

<h3>Features (<code>ml/features.py</code>)</h3>
<table>
  <tr><th>Feature</th><th>Fenêtre</th><th>Ce qu'elle capte</th></tr>
  <tr><td class="m">gas_dev_rel</td><td>ligne de base EWMA ≈ 5 min</td><td>écart relatif du gaz à sa ligne de base (micro-déviation, fuite)</td></tr>
  <tr><td class="m">gas_slope_30s</td><td>30 s</td><td>pente du gaz (spray butane : montée brutale)</td></tr>
  <tr><td class="m">gas_std_30s</td><td>30 s</td><td>turbulence du panache</td></tr>
  <tr><td class="m">gas_slope_2m</td><td>2 min</td><td>dérive lente du gaz</td></tr>
  <tr><td class="m">temp_slope_2m</td><td>2 min</td><td>échauffement lent (°C/min)</td></tr>
  <tr><td class="m">temp_dev</td><td>ligne de base EWMA ≈ 10 min</td><td>écart de température</td></tr>
  <tr><td class="m">hum_slope_2m</td><td>2 min</td><td>variation d'humidité (assèchement lors d'un échauffement)</td></tr>
  <tr><td class="m">gas_temp_corr</td><td>2 min</td><td>corrélation gaz × température : hausse lente de T° et micro-déviation de gaz</td></tr>
</table>
<p><b>Features directionnelles.</b> L'Isolation Forest est symétrique par nature. Avant le modèle, <code>vectorize()</code> ne conserve
que le sens du danger (hausse de gaz, échauffement, chute d'humidité, corrélation gaz-température positive) et met à zéro les baisses
et les retours à la normale. Une décroissance bénigne, comme le gaz qui redescend après un essai, n'est plus prise pour une anomalie.
Autres garde-fous : filtre médian sur 3 échantillons contre les glitchs ADC ; ligne de base presque gelée (×0,05) pendant une anomalie
pour ne pas « absorber » une fuite ; réinitialisation de l'état au redémarrage de l'ESP (chauffe du MQ-2).</p>

<h3>Décision et actions</h3>
<ol>
  <li>Score d'anomalie = <code>-score_samples()</code> sur features standardisées.</li>
  <li>Risque 0-100 interpolé entre la médiane normale (0), la vigilance q98 (30), le seuil d'anomalie appris (50) et un incident extrême de référence (100).</li>
  <li>Type d'incident selon la contribution des features (z-scores robustes médiane/MAD) : groupe gaz → <code>gaz_fumee</code>, groupe thermique → <code>thermique</code>, sinon <code>anomalie</code>.</li>
  <li>Persistance : 4 fenêtres anormales consécutives (≈ 8 s) pour lever l'incident, 5 normales pour le clore.</li>
  <li>Prédiction (pré-alerte sans son) si le risque lissé monte en zone de vigilance dans le sens d'un danger.</li>
</ol>
<table>
  <tr><th>État</th><th>Action</th></tr>
  <tr><td class="m">prediction</td><td>alerte <code>ia_prediction/risque_croissant</code> sur le journal et le dashboard, sans son</td></tr>
  <tr><td class="m">gaz_fumee</td><td>alerte <code>ia_gaz</code>, sirène <code>gaz</code> en boucle, LED rouge fixe ; réarmement 60 s après « Couper alarme » si le gaz persiste</td></tr>
  <tr><td class="m">thermique</td><td>alerte <code>ia_thermique</code>, LED rouge, bandeau dashboard</td></tr>
  <tr><td class="m">anomalie</td><td>alerte <code>ia_anomalie/anomalie_cinetique</code> (journal et dashboard)</td></tr>
</table>

<h3>Évaluation (<code>ml/evaluation.md</code>, généré le 5 octobre 2026 à 14:56, graine 42)</h3>
<p>Entraînement sur 1 004 fenêtres réelles de <code>sentinel-node-01</code> (5 octobre 2026, 11:40 → 14:14, heure de Paris)
et 4 016 fenêtres augmentées (bruit MQ-2 ±10 ADC, DHT22 ±0,5 °C / ±2 %RH, scénarios « personne proche » traités comme normaux).
Les essais réels (briquet, chauffage du DHT22, capteur débranché, chauffes après reboot) sont exclus via <code>ml/training_exclusions.json</code>.
Split temporel 80 / 20.</p>
<div class="strip">
  <div class="s"><span class="l">Rappel spray butane (≤ 12 s)</span><span class="v a">20/20</span></div>
  <div class="s"><span class="l">Latence alarme</span><span class="v">8 s</span><span class="l">médiane · max 10 s</span></div>
  <div class="s"><span class="l">Faux incidents (holdout)</span><span class="v">0</span><span class="l">192 fenêtres · 0,11 h</span></div>
  <div class="s"><span class="l">Glitchs isolés ignorés</span><span class="v">20/20</span></div>
  <div class="s"><span class="l">Dérives lentes avant règle</span><span class="v">4/4</span></div>
</div>
<figure class="n78"><img src="file://{ASSETS}/ia-avance.png"><figcaption>Fig. 3 — Dérives lentes simulées : première pré-alerte IA comparée au franchissement d'une règle statique (ml/evaluation.md §3).</figcaption></figure>
<table>
  <tr><th>Scénario</th><th>Pré-alerte IA</th><th>Incident confirmé</th><th>Règle statique</th><th>Avance IA</th></tr>
  <tr><td>Fuite lente +20 ADC/min</td><td class="m">0,7 min</td><td class="m">0,8 min (gaz_fumee)</td><td class="m">11,5 min (gaz &gt; 300)</td><td class="m">10,8 min</td></tr>
  <tr><td>Fuite lente +8 ADC/min</td><td class="m">0,8 min</td><td class="m">non</td><td class="m">28,7 min (gaz &gt; 300)</td><td class="m">27,9 min</td></tr>
  <tr><td>Échauffement +0,5 °C/min, gaz +2 ADC/min</td><td class="m">0,9 min</td><td class="m">6,2 min (thermique)</td><td class="m">32,0 min (T &gt; 40 °C)</td><td class="m">31,1 min</td></tr>
  <tr><td>Échauffement +0,3 °C/min, gaz +1 ADC/min</td><td class="m">0,9 min</td><td class="m">6,7 min (anomalie)</td><td class="m">53,3 min (T &gt; 40 °C)</td><td class="m">52,4 min</td></tr>
</table>
<p>Rejeu de l'historique réel (2 865 mesures, 11:40 → 14:49, heure de Paris) : 7 incidents ou escalades, dont 6 <code>gaz_fumee</code>
correspondant aux essais de gaz réalisés, et 10 épisodes de pré-alerte.</p>
<div class="note plain"><b>Limites.</b> L'historique normal réel couvre quelques heures d'une seule journée et le holdout normal
seulement 0,11 h : le taux de faux positifs reste à confirmer sur une durée plus longue. Les incidents <code>thermique</code> et <code>anomalie</code>
du rejeu sont à traiter comme « à vérifier ». Réentraîner après plusieurs heures de fonctionnement normal en étiquetant les essais.</div>

<h2>4.2 Vision — YOLOv8n</h2>
<p>Le service <code>sentinel-vision</code> (systemd utilisateur, <code>vision/vision_service.py</code>) lit la webcam UGREEN par son chemin stable
<code>/dev/v4l/by-id/…</code>, capture en 1280×720 MJPEG puis redimensionne en <b>782×440</b> avant l'inférence. Ce bridage en amont tient la
contrainte du sujet (moins de 100 ms par trame). YOLOv8n tourne sur CPU, limité à la classe « personne » avec une confiance minimale de 0,45 ;
l'inférence est cadencée à 0,12 s au plus (≈ 8 FPS).</p>
<div class="strip">
  <div class="s"><span class="l">Inférence moyenne (30 trames)</span><span class="v a">49,0 ms</span></div>
  <div class="s"><span class="l">Dernière trame</span><span class="v">44,8 ms</span></div>
  <div class="s"><span class="l">Pic ponctuel (≈ 1 h)</span><span class="v">105 ms</span></div>
  <div class="s"><span class="l">Débit inférence / flux</span><span class="v">20,4 · 9,8</span><span class="l">inf/s · FPS</span></div>
  <div class="s"><span class="l">Résolution</span><span class="v">782×440</span></div>
</div>
<p class="lead">Relevé sur <code>/health</code> du service vision, 5 octobre 2026 vers 15:00.</p>
<p>Le temps d'inférence s'affiche en incrustation sur le flux et dans le bloc « Vision IA » du dashboard (<code>infer_ms</code>). Sur
<code>person_detected</code>, le service publie une alerte (<code>POST /api/v1/alerts</code>, Bearer). Si l'alarme présence est armée, l'API
déclenche la sirène <code>intrus</code> pendant 15 s. Pendant une alerte gaz, la sirène intrus est inhibée.</p>

<!-- ======================= ANNEXE A3 ======================= -->
<section class="poster" id="a3">
  <div class="ph">
    <div><div class="t">SENTINEL<span>-X</span></div><div class="sub">Nœud IoT · stack Docker · MQTTS/HTTPS · Isolation Forest · YOLOv8n</div></div>
    <div class="r">Annexe A3 · Workshop EPSI Bac+4 · octobre 2026<br>Option B — PC apprenant = serveur<br>Workshop2026-{GLABEL}</div>
  </div>
  <div class="kpi">
    <div class="s"><span class="l">Rappel spray butane</span><span class="v a">20/20</span><span class="d">alarme en 8 s (médiane)</span></div>
    <div class="s"><span class="l">Avance sur règle statique</span><span class="v">11–52 min</span><span class="d">4/4 dérives lentes</span></div>
    <div class="s"><span class="l">Faux incidents</span><span class="v">0</span><span class="d">holdout normal 0,11 h</span></div>
    <div class="s"><span class="l">Inférence YOLOv8n</span><span class="v">49 ms</span><span class="d">CPU · 782×440 · &lt; 100 ms</span></div>
    <div class="s"><span class="l">Flux chiffrés</span><span class="v">TLS 1.3</span><span class="d">MQTTS 8883 · HTTPS 443</span></div>
    <div class="s"><span class="l">Ports SENTINEL exposés</span><span class="v">2</span><span class="d">443 · 8883 (sous-réseau de table)</span></div>
  </div>
  <div class="grid">
    <div style="width:42%">
      <h4>Architecture</h4>
      <img src="file://{ASSETS}/schema-reseau.png">
      <p style="margin-top:3mm">ESP8266 → MQTTS → Mosquitto → API FastAPI → PostgreSQL / WebSocket → dashboard HTTPS. Le service IA s'abonne à la
      télémétrie et pilote la sirène. La vision YOLO tourne sur le PC, à côté de la webcam.</p>
    </div>
    <div style="width:29%">
      <h4>Nœud de table</h4>
      <img src="file://{ASSETS}/schema-cablage.png">
      <table style="margin-top:3mm">
        <tr><th>Capteur / actionneur</th><th>Broche</th></tr>
        <tr><td>DHT22 · OLED I2C</td><td class="m">D5 · D1/D2</td></tr>
        <tr><td>PIR HW-416A · MQ-2</td><td class="m">D6 · A0 (5 V)</td></tr>
        <tr><td>Buzzer · LED rouge · LED verte</td><td class="m">D7 · D0 · D8</td></tr>
      </table>
      <h4 style="margin-top:4mm">Sécurité</h4>
      <ul>
        <li>MQTTS + pin du certificat, ACL par rôle, anonyme refusé</li>
        <li>HTTPS Caddy, session HttpOnly, rate-limit, caméra authentifiée</li>
        <li>UFW deny par défaut + DOCKER-USER</li>
        <li>Conteneurs read-only, cap_drop ALL, limites</li>
      </ul>
    </div>
    <div style="width:29%">
      <h4>IA prédictive</h4>
      <p>Isolation Forest, 300 arbres, 8 features cinétiques directionnelles. Pas de seuil fixe : le risque 0-100 est calibré sur les quantiles appris du score.</p>
      <img src="file://{ASSETS}/ia-avance.png">
      <h4 style="margin-top:4mm">Réponse</h4>
      <table>
        <tr><td class="m">prediction</td><td>pré-alerte silencieuse</td></tr>
        <tr><td class="m">gaz_fumee</td><td>sirène gaz + LED rouge</td></tr>
        <tr><td class="m">thermique</td><td>LED rouge + bandeau</td></tr>
        <tr><td class="m">intrusion</td><td>sirène intrus 15 s (si armée)</td></tr>
      </table>
    </div>
  </div>
  <div class="chain">
    <div><b>01 · CAPTER</b>DHT22, MQ-2, PIR, OLED d'état ; webcam USB sur le PC serveur</div>
    <div><b>02 · TRANSPORTER</b>MQTTS 8883, pin du certificat, comptes et ACL par rôle</div>
    <div><b>03 · ANALYSER</b>Isolation Forest sur 8 dynamiques capteurs ; YOLOv8n ≈ 49 ms par trame</div>
    <div><b>04 · ALERTER</b>sirène gaz / intrus, LED, dashboard HTTPS temps réel, journal PostgreSQL</div>
  </div>
</section>
</body></html>"""

html_path = f"{ASSETS}/dossier.html"
open(html_path, "w", encoding="utf-8").write(HTMLDOC)
HTML(string=HTMLDOC, base_url=ASSETS).write_pdf(OUT)
print(OUT)
