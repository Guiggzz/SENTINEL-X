"""SENTINEL-X — présentation soutenance (python-pptx), DA du dashboard / dossier."""
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn

ROOT = os.path.expanduser("~/sentinel-x")
ASSETS = f"{ROOT}/docs/assets"
GROUP = os.environ.get("SENTINEL_GROUP", "")
GLABEL = f"M1-G{GROUP}" if GROUP else "M1-G__"
OUT = f"{ROOT}/docs/Workshop2026-M1-G{GROUP}-Pres.pptx"

C = lambda h: RGBColor.from_string(h)
BG, INK, MUTED = C("F2F1EC"), C("111111"), C("6B6A64")
RULE, RULE_S, SURF = C("D4D2C8"), C("B8B6AB"), C("EAE9E2")
ACC, OK = C("C45C26"), C("2F7D4A")
SANS, MONO = "Ubuntu", "DejaVu Sans Mono"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
W, H = 13.333, 7.5
L, R = 0.75, 13.333 - 0.75          # marges
BLANK = prs.slide_layouts[6]
TOTAL = 9


def _nostyle(shape):
    st = shape._element.find(qn("p:style"))
    if st is not None:
        shape._element.remove(st)


def text(sl, x, y, w, h, runs, size=14, color=INK, font=SANS, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, spacing=1.15, after=0):
    """runs: str | list of paragraphs; a paragraph is str or list of (txt, {opts})."""
    tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        p.space_after = Pt(after)
        segs = para if isinstance(para, list) else [(para, {})]
        for seg, o in segs:
            r = p.add_run()
            r.text = seg
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.color.rgb = o.get("color", color)
    return tb


def rule(sl, x1, y, x2, color=RULE, wt=0.75, y2=None):
    ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y), Inches(x2), Inches(y if y2 is None else y2))
    _nostyle(ln)
    ln.line.color.rgb = color
    ln.line.width = Pt(wt)
    return ln


def box(sl, x, y, w, h, fill=None, line=None, wt=0.75, shape=MSO_SHAPE.RECTANGLE):
    s = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    _nostyle(s)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid(); s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line; s.line.width = Pt(wt)
    return s


def dot(sl, x, y, color, d=0.11):
    return box(sl, x, y, d, d, fill=color, shape=MSO_SHAPE.OVAL)


def picture(sl, path, x, y, w=None, h=None, border=True):
    if not os.path.exists(path):
        f = box(sl, x, y, w or 6, h or 4, line=RULE_S)
        f.line.dash_style = 4
        text(sl, x, y + (h or 4) / 2 - 0.2, w or 6, 0.4, f"[image manquante : {os.path.basename(path)}]",
             size=11, color=MUTED, font=MONO, align=PP_ALIGN.CENTER)
        return f
    kw = {}
    if w: kw["width"] = Inches(w)
    if h: kw["height"] = Inches(h)
    pic = sl.shapes.add_picture(path, Inches(x), Inches(y), **kw)
    if border:
        pic.line.color.rgb = RULE; pic.line.width = Pt(0.75)
    return pic


def new_slide(n, num=None, title=None, lead=None):
    sl = prs.slides.add_slide(BLANK)
    sl.background.fill.solid(); sl.background.fill.fore_color.rgb = BG
    if n > 1:
        text(sl, L, 0.38, 6, 0.25, "SENTINEL-X · Option B — PC serveur", size=9, color=MUTED, font=MONO)
        text(sl, R - 4, 0.38, 4, 0.25, f"Workshop2026-{GLABEL}", size=9, color=MUTED, font=MONO, align=PP_ALIGN.RIGHT)
        rule(sl, L, 0.70, R)
        text(sl, L, H - 0.50, 7, 0.25, "AetherCorp · EPSI Workshop Bac+4 · octobre 2026", size=9, color=MUTED, font=MONO)
        text(sl, R - 2, H - 0.50, 2, 0.25, f"{n:02d} / {TOTAL:02d}", size=9, color=INK, font=MONO, align=PP_ALIGN.RIGHT)
    if title:
        text(sl, L, 1.00, R - L, 0.7, [[(f"{num}", {"font": MONO, "color": ACC, "size": 26}),
                                         ("   " + title, {"size": 28, "bold": True})]], anchor=MSO_ANCHOR.BOTTOM)
        rule(sl, L, 1.82, R, color=INK, wt=1.0)
        if lead:
            text(sl, L, 1.98, R - L, 0.4, lead, size=14, color=MUTED)
    return sl


def strip(sl, y, items, x=L, w=None, h=1.05):
    """Bandeau de chiffres : items = [(label, valeur, accent?, détail)]."""
    w = w or (R - x)
    cw = w / len(items)
    rule(sl, x, y, x + w, color=INK, wt=1.0)
    rule(sl, x, y + h, x + w)
    for i, (lab, val, acc, det) in enumerate(items):
        cx = x + i * cw
        if i:
            rule(sl, cx, y + 0.12, cx, y2=y + h - 0.12)
        text(sl, cx + 0.18, y + 0.14, cw - 0.3, 0.25, lab, size=10, color=MUTED)
        text(sl, cx + 0.18, y + 0.38, cw - 0.3, 0.42, val, size=22, font=MONO, color=ACC if acc else INK)
        if det:
            text(sl, cx + 0.18, y + 0.78, cw - 0.3, 0.22, det, size=9, color=MUTED, font=MONO)


def table(sl, x, y, cols, rows, widths, rh=0.42, size=12, mono_cols=(), head_size=10):
    """Tableau « filet » : en-tête muted, filet encre, filets fins entre lignes."""
    cx = [x]
    for wd in widths[:-1]:
        cx.append(cx[-1] + wd)
    tw = sum(widths)
    for j, c in enumerate(cols):
        text(sl, cx[j], y, widths[j] - 0.1, 0.3, c, size=head_size, color=MUTED, anchor=MSO_ANCHOR.BOTTOM)
    yy = y + 0.36
    rule(sl, x, yy, x + tw, color=INK, wt=1.0)
    for row in rows:
        for j, cell in enumerate(row):
            if callable(cell):
                cell(sl, cx[j], yy, widths[j], rh)
            else:
                text(sl, cx[j], yy, widths[j] - 0.12, rh, cell, size=size, anchor=MSO_ANCHOR.MIDDLE,
                     font=MONO if j in mono_cols else SANS)
        yy += rh
        rule(sl, x, yy, x + tw)
    return yy


def tagcell(kind, label):
    col = {"ok": OK, "part": RULE_S, "ko": ACC}[kind]
    def draw(sl, x, y, w, h):
        d = dot(sl, x, y + h / 2 - 0.055, col)
        if kind == "part":
            d.line.color.rgb = ACC; d.line.width = Pt(1)
        text(sl, x + 0.22, y, w - 0.3, h, label.upper(), size=10, font=MONO,
             color=ACC if kind == "ko" else INK, anchor=MSO_ANCHOR.MIDDLE)
    return draw


def notes(sl, txt):
    sl.notes_slide.notes_text_frame.text = txt


# ---------------------------------------------------------------- 1 · Titre
sl = new_slide(1)
text(sl, L, 0.55, 6, 0.3, "AetherCorp · Centre de surveillance de table", size=11, color=MUTED, font=MONO)
text(sl, R - 5, 0.55, 5, 0.3, f"Workshop2026-{GLABEL}", size=11, color=MUTED, font=MONO, align=PP_ALIGN.RIGHT)
rule(sl, L, 0.95, R)
box(sl, L, 2.05, 0.9, 0.06, fill=ACC)
text(sl, L, 2.30, 10, 0.35, "MISSION · SOUTENANCE TECHNIQUE", size=12, color=MUTED, font=MONO)
text(sl, L, 2.70, 11, 1.3, [[("SENTINEL", {}), ("-X", {"color": ACC})]], size=80, bold=True)
text(sl, L, 4.15, 10, 0.5, "Nœud IoT ESP8266 · stack Docker chiffrée · IA prédictive · vision embarquée", size=18, color=MUTED)
yy = 5.05
for k, v, m in [("Option", "B — PC serveur (Edge-to-Server)", False),
                ("Cadre", "EPSI · Workshop Bac+4 (M1) · octobre 2026", False),
                ("Groupe", GLABEL, True)]:
    rule(sl, L, yy, L + 7.2)
    text(sl, L, yy + 0.09, 1.6, 0.3, k, size=11, color=MUTED)
    text(sl, L + 1.6, yy + 0.07, 5.6, 0.3, v, size=13, font=MONO if m else SANS)
    yy += 0.42
rule(sl, L, yy, L + 7.2)
rule(sl, L, H - 0.70, R)
text(sl, L, H - 0.55, 7, 0.3, [[("Sentinel-X : ", {}), (".", {"color": ACC})]], size=12, bold=True)
text(sl, R - 5, H - 0.55, 5, 0.3, "Document sans secret", size=10, color=MUTED, font=MONO, align=PP_ALIGN.RIGHT)
notes(sl, "Accroche : AetherCorp, micro-centrales isolées. SENTINEL-X = boîtier autonome + PC serveur local durci (Option B).")

# ---------------------------------------------------------------- 2 · Contexte
sl = new_slide(2, "01", "Contexte AetherCorp",
               "Micro-centrales en zones isolées, sans personnel permanent : trois menaces simultanées.")
cw = (R - L - 0.8) / 3
for i, (n, t, d, rep) in enumerate([
        ("01", "Cyberattaques", "Déstabilisation du réseau : écoute, injection de commandes, saturation du broker.",
         "MQTTS 8883 · HTTPS 443 · UFW"),
        ("02", "Intrusions physiques", "Espionnage industriel sur site, accès au boîtier et aux installations.",
         "Webcam + YOLOv8n · sirène intrus"),
        ("03", "Risques environnementaux", "Fuites de gaz combustibles, surchauffes thermiques lentes ou brutales.",
         "MQ-2 + DHT22 · Isolation Forest")]):
    x = L + i * (cw + 0.4)
    rule(sl, x, 2.85, x + cw, color=INK, wt=1.0)
    text(sl, x, 3.05, cw, 0.6, n, size=34, font=MONO, color=ACC)
    text(sl, x, 3.80, cw, 0.5, t, size=19, bold=True)
    text(sl, x, 4.40, cw, 1.2, d, size=14, color=MUTED, spacing=1.25)
    rule(sl, x, 5.75, x + cw)
    text(sl, x, 5.88, cw, 0.3, "Réponse SENTINEL-X", size=10, color=MUTED)
    text(sl, x, 6.15, cw, 0.35, rep, size=12, font=MONO)
notes(sl, "Les trois menaces du sujet, chacune associée à une brique du prototype.")

# ---------------------------------------------------------------- 3 · Architecture
sl = new_slide(3, "02", "Architecture", "Option B : le PC apprenant héberge toute la stack ; le boîtier ne contient que l'ESP8266.")
picture(sl, f"{ASSETS}/schema-reseau.png", L, 2.55, h=4.25)
x = L + 4.25 * 2420 / 1408 + 0.45
wd = R - x
yy = 2.6
for lab, val in [("Capteurs → broker", "ESP8266 → MQTTS 8883"),
                 ("Opérateur → dashboard", "Navigateur → HTTPS 443"),
                 ("Données", "PostgreSQL 16, aucun port hôte"),
                 ("Vision", "Webcam USB → YOLOv8n sur le PC"),
                 ("Réseau de table", "172.20.10.0/28 · 14 hôtes")]:
    rule(sl, x, yy, R)
    text(sl, x, yy + 0.08, wd, 0.25, lab, size=10, color=MUTED)
    text(sl, x, yy + 0.33, wd, 0.35, val, size=12, font=MONO)
    yy += 0.82
rule(sl, x, yy, R)
notes(sl, "Flux entrants autorisés : 8883 depuis le sous-réseau de table, 443. API (3000) et vision (8081) non exposées au LAN.")

# ---------------------------------------------------------------- 4 · Boîtier & câblage
sl = new_slide(4, "03", "Boîtier & câblage", "NodeMCU v3 alimentée en USB, masse commune, 5 V pris sur VU.")
picture(sl, f"{ASSETS}/schema-cablage.png", L, 2.55, h=4.25)
x = L + 4.25 * 2420 / 1452 + 0.45
table(sl, x, 2.45, ["Composant", "Broche"],
      [["DHT22", "D5"], ["PIR HW-416A", "D6"], ["MQ-2 (5 V)", "A0"], ["OLED SSD1306", "D1 · D2"],
       ["Buzzer", "D7"], ["LED rouge", "D0"], ["LED verte", "D8"]],
      [R - x - 1.3, 1.3], rh=0.40, size=12, mono_cols=(1,))
text(sl, x, 6.0, R - x, 0.7, [[("! ", {"color": ACC, "font": MONO, "bold": True}),
                               ("D8 bas au démarrage · AO du MQ-2 ≤ 3,3 V", {})]], size=11, color=MUTED)
notes(sl, "Broches relevées dans firmware/sentinel_node/sentinel_node.ino. OLED en I2C 0x3C, affiche IP et état MQTTS.")

# ---------------------------------------------------------------- 5 · Stack Docker
sl = new_slide(5, "04", "Stack Docker + MQTTS / HTTPS", "Cinq conteneurs, deux ports exposés, tout le trafic chiffré en TLS 1.3.")
table(sl, L, 2.55, ["Conteneur", "Image", "Port hôte"],
      [["sentinel-mosquitto", "eclipse-mosquitto:2.0.20", "8883"],
       ["sentinel-proxy", "caddy:2.8-alpine", "443"],
       ["sentinel-api", "FastAPI (build)", "127.0.0.1:3000"],
       ["sentinel-ml", "scikit-learn (build)", "—"],
       ["sentinel-db", "postgres:16-alpine", "—"]],
      [2.6, 3.0, 1.9], rh=0.46, size=12, mono_cols=(0, 2))
x = L + 7.5 + 0.6
wd = R - x
for i, (t, items) in enumerate([("MQTTS · 8883", ["TLS 1.3 · anonyme refusé", "Pin du certificat sur l'ESP", "Comptes + ACL par rôle"]),
                                ("HTTPS · 443", ["Caddy · HSTS · CSP", "Session HttpOnly / Secure", "Rate-limit login et commandes"])]):
    y = 2.55 + i * 2.05
    box(sl, x, y, wd, 1.8, fill=SURF, line=RULE)
    text(sl, x + 0.25, y + 0.18, wd - 0.5, 0.35, t, size=16, font=MONO, color=ACC)
    text(sl, x + 0.25, y + 0.65, wd - 0.5, 1.1, items, size=12, spacing=1.2, after=2)
notes(sl, "Limites CPU/RAM/PID par conteneur, logs en rotation 10 Mo × 3. MCO du dashboard via /api/v1/system.")

# ---------------------------------------------------------------- 6 · IA
sl = new_slide(6, "05", "IA : Isolation Forest + YOLO",
               "Aucun seuil statique : le modèle apprend la dynamique normale des capteurs. Vision sous 100 ms par trame.")
strip(sl, 2.55, [("Rappel spray butane", "20/20", True, "alarme 8 s médiane"),
                 ("Faux incidents", "0", False, "holdout normal"),
                 ("Avance / règle statique", "11–52 min", False, "4/4 dérives lentes"),
                 ("Inférence YOLOv8n", "49 ms", True, "CPU · < 100 ms")])
text(sl, L, 3.95, 4.6, 0.3, "Maintenance prédictive", size=10, color=MUTED)
text(sl, L, 4.25, 4.6, 1.6, ["Isolation Forest · 300 arbres", "8 features cinétiques directionnelles",
                             "Risque 0-100 calibré sur quantiles"], size=13, spacing=1.2, after=3)
text(sl, L, 5.45, 4.6, 0.3, "Vision", size=10, color=MUTED)
text(sl, L, 5.75, 4.6, 1.0, ["YOLOv8n CPU · 782×440", "classe personne · confiance ≥ 0,45"], size=13, spacing=1.2, after=3)
picture(sl, f"{ASSETS}/ia-avance.png", L + 5.0, 4.05, w=R - L - 5.0)
notes(sl, "Chiffres issus de ml/evaluation.md (5 oct. 2026) et du /health du service vision.")

# ---------------------------------------------------------------- 7 · Sécurité
sl = new_slide(7, "06", "Sécurité / hardening", "Défense en profondeur : chiffrement, authentification, filtrage, conteneurs restreints.")
table(sl, L, 2.45, ["Mesure", "État", "Preuve"],
      [["MQTTS seul, port 1883 fermé", tagcell("ok", "Fait"), "openssl s_client → TLSv1.3"],
       ["Pin du certificat côté ESP", tagcell("ok", "Fait"), "setFingerprint()"],
       ["HTTPS Caddy + en-têtes", tagcell("ok", "Fait"), "HSTS · CSP · X-Frame DENY"],
       ["Auth dashboard + rate-limit", tagcell("ok", "Fait"), "WebSocket 4401 sans session"],
       ["Caméra derrière authentification", tagcell("ok", "Fait"), "/cam/health → 401"],
       ["UFW deny + DOCKER-USER", tagcell("ok", "Fait"), "8883 limité au /28"],
       ["Conteneurs read-only, cap_drop", tagcell("part", "Partiel"), "api · ml · proxy"],
       ["Docker rootless", tagcell("ko", "Non fait"), "recommandation"]],
      [4.6, 2.2, R - L - 6.8], rh=0.48, size=13)
notes(sl, "Preuves : infra/hardening/verify.sh → docs/preuves-securite.txt. Points ouverts : rootless, filtrage des ports des autres projets.")

# ---------------------------------------------------------------- 8 · Démo
sl = new_slide(8, "07", "Démo live : checklist", "Ordre de passage devant le jury.")
yy = 2.55
for i, (t, cmd) in enumerate([("Stack up / healthy", "docker compose ps"),
                              ("Nœud connecté en TLS", "OLED : MQTTS: OK"),
                              ("Dashboard opérateur, télémétrie live", "https://<ip-pc>/"),
                              ("Écoute réseau : charge illisible", "wireshark tcp.port==8883"),
                              ("Spray butane → pré-alerte puis sirène gaz", "≈ 8 s"),
                              ("Intrus devant la webcam → sirène intrus", "YOLO ≈ 49 ms"),
                              ("Scan de ports : surface minimale", "nmap → 443 · 8883")]):
    box(sl, L, yy + 0.13, 0.22, 0.22, line=INK, wt=1.0)
    text(sl, L + 0.45, yy + 0.06, 0.6, 0.35, f"{i + 1:02d}", size=13, font=MONO, color=ACC)
    text(sl, L + 1.05, yy + 0.06, 6.6, 0.35, t, size=15)
    text(sl, L + 7.8, yy + 0.08, R - L - 7.8, 0.35, cmd, size=12, font=MONO, color=MUTED)
    yy += 0.55
    rule(sl, L, yy, R)
notes(sl, "Lancer verify.sh avant le passage. Plan B : vidéo de démonstration si le réseau de table tombe.")

# ---------------------------------------------------------------- 9 · Équipe
sl = new_slide(9, "08", "Équipe · questions", "Consortium d'ingénieurs AetherCorp.")
table(sl, L, 2.55, ["Membre", "Filière", "Rôle principal"],
      [["Nathanaël Lejuste", "DEV", "API"],
       ["Rémy Eroes", "DEV", "Dashboard & système embarqué"],
       ["Amaury Malisova", "DEV", "IA"],
       ["Guillaume Breon", "DEV", "Dashboard & système embarqué"]],
      [3.0, 1.3, 3.6], rh=0.55, size=14, mono_cols=(1,))
x = L + 8.3
box(sl, x, 2.55, 0.06, 0.9, fill=ACC)
text(sl, x + 0.35, 2.45, R - x - 0.35, 1.1, "Questions ?", size=34, bold=True)
text(sl, x + 0.35, 3.55, R - x - 0.35, 0.4, f"Workshop2026-{GLABEL}", size=12, font=MONO, color=MUTED)
rule(sl, x + 0.35, 5.6, R)
text(sl, x + 0.35, 5.75, R - x - 0.35, 0.6, [[("Sentinel-X : ", {}), (".", {"color": ACC})]],
     size=15, bold=True)
notes(sl, "Merci. Ouverture aux questions du jury.")

prs.core_properties.title = "SENTINEL-X — Option B PC serveur"
prs.core_properties.author = "Workshop2026-" + GLABEL
prs.save(OUT)
print(OUT, len(prs.slides._sldIdLst), "slides")
