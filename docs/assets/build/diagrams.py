"""SENTINEL-X — génération des schémas (réseau, câblage) dans la DA du dashboard."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch
from matplotlib import font_manager

BG, INK, MUTED, RULE, RULE_S, SURF, ACC, OK = (
    "#f2f1ec", "#111111", "#6b6a64", "#d4d2c8", "#b8b6ab", "#eae9e2", "#c45c26", "#2f7d4a")
OUT = os.path.expanduser("~/sentinel-x/docs/assets")
os.makedirs(OUT, exist_ok=True)

avail = {f.name for f in font_manager.fontManager.ttflist}
SANS = next((f for f in ["Ubuntu Sans", "Ubuntu", "DejaVu Sans"] if f in avail), "DejaVu Sans")
MONO = "DejaVu Sans Mono"
plt.rcParams.update({"font.family": SANS, "svg.fonttype": "none"})


def canvas(w, h, xr, yr):
    fig = plt.figure(figsize=(w, h), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, xr); ax.set_ylim(0, yr); ax.axis("off"); ax.set_facecolor(BG)
    return fig, ax


def box(ax, x, y, w, h, title, sub=None, accent=False, fill=BG, dashed=False, tsize=8.6, ssize=7.0, align="center"):
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=ACC if accent else RULE_S,
                           linewidth=0.9, linestyle=(0, (3, 2)) if dashed else "solid", zorder=2))
    cx = x + w / 2 if align == "center" else x + 0.8
    ha = "center" if align == "center" else "left"
    if sub:
        ax.text(cx, y + h * 0.66, title, ha=ha, va="center", fontsize=tsize, color=INK, weight="semibold", zorder=3)
        ax.text(cx, y + h * 0.30, sub, ha=ha, va="center", fontsize=ssize, color=MUTED, family=MONO,
                zorder=3, linespacing=1.35)
    else:
        ax.text(cx, y + h / 2, title, ha=ha, va="center", fontsize=tsize, color=INK, weight="semibold", zorder=3)


def arrow(ax, p1, p2, label=None, accent=False, both=False, lpos=0.5, loff=(0, 1.0), lsize=6.6, ha="center"):
    c = ACC if accent else INK
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="<|-|>" if both else "-|>", mutation_scale=7,
                                 color=c, linewidth=0.9, zorder=4, shrinkA=0, shrinkB=0))
    if label:
        lx = p1[0] + (p2[0] - p1[0]) * lpos + loff[0]
        ly = p1[1] + (p2[1] - p1[1]) * lpos + loff[1]
        ax.text(lx, ly, label, ha=ha, va="center", fontsize=lsize, color=c, family=MONO, zorder=5,
                bbox=dict(facecolor=BG, edgecolor="none", pad=0.6))


# ------------------------------------------------------------------ réseau
def network():
    fig, ax = canvas(11.0, 6.4, 100, 60)
    ax.add_patch(Rectangle((1, 1), 98, 58, fill=False, edgecolor=RULE_S, linewidth=0.8, linestyle=(0, (4, 3))))
    ax.text(2.2, 57.2, "RÉSEAU DE TABLE  ·  hotspot iPhone  ·  172.20.10.0/28 (actuel)",
            fontsize=7.4, color=MUTED, family=MONO, va="center")

    box(ax, 4, 38, 18, 12, "ESP8266 NodeMCU v3", "sentinel-node-01\n172.20.10.2 (DHCP)")
    box(ax, 4, 10, 18, 11, "Poste opérateur / jury", "navigateur\nCA locale importée")
    box(ax, 28, 24, 14, 12, "Hotspot iPhone", "passerelle .1\n/28 · 14 hôtes", fill=SURF)

    ax.add_patch(Rectangle((47, 3.5), 50.5, 51.5, facecolor=BG, edgecolor=INK, linewidth=1.0, zorder=1))
    ax.text(48.4, 52.8, "PC serveur — Option B  ·  172.20.10.4  ·  Ubuntu", fontsize=8.6, weight="semibold", color=INK)
    ax.add_patch(Rectangle((48.5, 5), 3.6, 44.5, facecolor=SURF, edgecolor=RULE_S, linewidth=0.8, zorder=2))
    ax.text(50.3, 15.5, "UFW deny-in\nDOCKER-USER", rotation=90, ha="center", va="center", fontsize=6.4,
            family=MONO, color=INK, zorder=3, linespacing=1.2)

    ax.add_patch(Rectangle((54.5, 20.5), 41.5, 30, facecolor=BG, edgecolor=RULE_S, linewidth=0.8, zorder=1.5))
    ax.text(55.5, 48.6, "Docker Compose  ·  réseaux sentinel-front / sentinel-back (internal)", fontsize=6.6,
            family=MONO, color=MUTED, va="center")

    box(ax, 56.5, 38, 12, 8, "sentinel-proxy", "Caddy · :443")
    box(ax, 56.5, 24.5, 12, 8, "sentinel-mosquitto", ":8883 TLS · ACL")
    box(ax, 71, 38, 12, 8, "sentinel-api", "FastAPI\n127.0.0.1:3000")
    box(ax, 84, 38, 11, 8, "sentinel-db", "PostgreSQL 16\naucun port hôte", fill=SURF)
    box(ax, 84, 24.5, 11, 8, "sentinel-ml", "Isolation\nForest")

    box(ax, 66, 6.5, 16, 9, "sentinel-vision", "systemd --user\nYOLOv8n · :8081")
    box(ax, 85, 6.5, 11, 9, "Webcam USB", "UGREEN\n782×440")

    arrow(ax, (22, 44), (28, 33), "Wi-Fi", lpos=0.45, loff=(-2.4, 0.4))
    arrow(ax, (22, 15.5), (28, 27), "Wi-Fi", lpos=0.45, loff=(-2.4, -0.4))
    arrow(ax, (42, 28.5), (56.5, 28.5), "MQTTS 8883", accent=True, both=True, lpos=0.2, loff=(0, 1.2))
    arrow(ax, (42, 33.5), (56.5, 42), "HTTPS 443", accent=True, lpos=0.3, loff=(-1.0, 1.4))
    arrow(ax, (68.5, 42), (71, 42))
    arrow(ax, (83, 42), (84, 42))
    arrow(ax, (62.5, 32.5), (71, 38.5), "TLS", both=True, lpos=0.55, loff=(-1.6, 0.6))
    arrow(ax, (68.5, 28.5), (84, 28.5), "telemetry → ai/#, cmd", both=True, lpos=0.5, loff=(0, 1.3))
    arrow(ax, (89.5, 32.5), (89.5, 38), "SQL", both=True, lpos=0.5, loff=(2.6, 0))
    arrow(ax, (77, 38), (77, 15.5), "/cam → :8081", lpos=0.88, loff=(0, 0))
    arrow(ax, (85, 11), (82, 11), "USB", lpos=0.5, loff=(0, 2.2))
    ax.text(56, 13.5, "MCO : /proc hôte\nlu par l'API", fontsize=6.4, family=MONO, color=MUTED, va="center")
    ax.text(2.2, 3.0, "Cible sujet : point d'accès dédié 192.168.10.0/24 (même schéma, sous-réseau étanche)",
            fontsize=6.8, color=MUTED, family=MONO, va="center")
    fig.savefig(f"{OUT}/schema-reseau.png", dpi=220, facecolor=BG)
    plt.close(fig)


# ------------------------------------------------------------------ câblage
def wiring():
    fig, ax = canvas(11.0, 6.6, 100, 60)
    bx, bw = 40, 18
    ax.add_patch(Rectangle((bx, 12), bw, 44, facecolor=SURF, edgecolor=INK, linewidth=1.0, zorder=2))
    ax.text(bx + bw / 2, 53, "NodeMCU v3", ha="center", fontsize=9, weight="semibold", color=INK, zorder=3)
    ax.text(bx + bw / 2, 50.6, "ESP8266 · USB 5 V", ha="center", fontsize=6.8, family=MONO, color=MUTED, zorder=3)
    ax.add_patch(Rectangle((bx + 6, 12), 6, 3, facecolor=BG, edgecolor=RULE_S, linewidth=0.8, zorder=3))
    ax.text(bx + 9, 13.5, "USB", ha="center", va="center", fontsize=5.8, family=MONO, color=MUTED, zorder=4)

    left = [("D0", 46, "GPIO16"), ("D1", 41.5, "SCL"), ("D2", 37, "SDA"), ("D5", 32.5, "GPIO14"),
            ("D6", 28, "GPIO12"), ("D7", 23.5, "GPIO13"), ("D8", 19, "GPIO15")]
    right = [("A0", 46, "ADC"), ("3V3", 37, ""), ("VU", 28, "5 V"), ("GND", 19, "")]
    for name, y, g in left:
        ax.plot([bx - 0.8, bx], [y, y], color=INK, lw=2.2, zorder=3, solid_capstyle="butt")
        ax.text(bx + 1.0, y, f"{name}", va="center", fontsize=7.6, family=MONO, color=INK, zorder=3, weight="bold")
        ax.text(bx + 5.0, y, g, va="center", fontsize=6.0, family=MONO, color=MUTED, zorder=3)
    for name, y, g in right:
        ax.plot([bx + bw, bx + bw + 0.8], [y, y], color=INK, lw=2.2, zorder=3, solid_capstyle="butt")
        ax.text(bx + bw - 1.0, y, name, va="center", ha="right", fontsize=7.6, family=MONO, color=INK, zorder=3,
                weight="bold")
        if g:
            ax.text(bx + bw - 6.4, y, g, va="center", ha="right", fontsize=6.0, family=MONO, color=MUTED, zorder=3)

    def wire(y, x0, x1, accent=False):
        ax.plot([x0, x1], [y, y], color=ACC if accent else INK, lw=0.9, zorder=2)

    def resistor(x, y, label="220–330 Ω"):
        ax.add_patch(Rectangle((x - 2.2, y - 0.9), 4.4, 1.8, facecolor=BG, edgecolor=INK, linewidth=0.8, zorder=4))
        ax.text(x, y + 1.9, label, ha="center", va="center", fontsize=5.8, family=MONO, color=MUTED, zorder=4)

    # composants gauche
    comps = [
        (46, 4.5, "LED rouge", "anode ← R ← D0 · K → GND", True),
        (32.5, 4.5, "DHT22", "DATA D5 · VCC 3V3 · GND", False),
        (28, 4.5, "PIR HW-416A", "OUT D6 · VCC 5 V (VU) · GND", False),
        (23.5, 4.5, "Buzzer piézo", "+ D7 · − GND", False),
        (19, 4.5, "LED verte", "anode ← R ← D8 · K → GND", True),
    ]
    for y, h, t, s, r in comps:
        box(ax, 2, y - h / 2 + 0.3, 24, h - 0.6, t, s, tsize=7.8, ssize=6.0, align="left")
        wire(y, 26, bx - 0.8)
        if r:
            resistor(33, y)
    # OLED (2 broches)
    box(ax, 2, 35.4, 24, 7.7, "OLED SSD1306 0,96\"", "I2C 0x3C · SCL D1 · SDA D2\nVCC 3V3 · GND", tsize=7.8, ssize=6.0,
        align="left")
    wire(41.5, 26, bx - 0.8); wire(37, 26, bx - 0.8)

    # MQ-2
    box(ax, 72, 43.5, 25, 5.4, "MQ-2 gaz / fumée", "AO → A0 · VCC 5 V (VU) · GND", tsize=7.8, ssize=6.0, align="left")
    wire(46, bx + bw + 0.8, 72)
    # rails d'alimentation
    rails = [(37, "3V3 → OLED, DHT22", False), (28, "VU 5 V → PIR, MQ-2", True),
             (19, "GND commun → tous modules, LED K, buzzer −", False)]
    for y, lbl, acc in rails:
        wire(y, bx + bw + 0.8, 97, accent=acc)
        ax.text(bx + bw + 3, y + 1.3, lbl, fontsize=6.6, family=MONO, color=ACC if acc else INK, va="center")
    ax.text(2, 9.0, "Encre : signaux 3,3 V   ·   Accent : alimentation 5 V (broche VU, USB)   ·   R = 220–330 Ω en série sur chaque LED",
            fontsize=6.6, family=MONO, color=MUTED)
    ax.text(2, 5.6, "Pins confirmées dans firmware/sentinel_node/sentinel_node.ino (#define PIN_*, Wire.begin(D2, D1))",
            fontsize=6.6, family=MONO, color=MUTED)
    fig.savefig(f"{OUT}/schema-cablage.png", dpi=220, facecolor=BG)
    plt.close(fig)


# ------------------------------------------------------------------ IA : avance vs règle statique
def ia_lead():
    # Données : ml/evaluation.md §3 (généré 2026-10-05T12:56:40Z)
    rows = [("Fuite lente +20 ADC/min", 0.7, 11.5, "gaz > 300"),
            ("Fuite lente +8 ADC/min", 0.8, 28.7, "gaz > 300"),
            ("Échauffement +0,5 °C/min, gaz +2/min", 0.9, 32.0, "T > 40 °C"),
            ("Échauffement +0,3 °C/min, gaz +1/min", 0.9, 53.3, "T > 40 °C")]
    fig = plt.figure(figsize=(8.6, 2.9), facecolor=BG)
    ax = fig.add_axes([0.30, 0.20, 0.66, 0.72]); ax.set_facecolor(BG)
    ys = list(range(len(rows)))[::-1]
    for y, (lbl, ia, st, rule) in zip(ys, rows):
        ax.barh(y + 0.17, st, height=0.3, color=RULE_S)
        ax.barh(y - 0.17, ia, height=0.3, color=ACC)
        ax.text(st + 0.8, y + 0.17, f"{st:.1f} min  ({rule})", va="center", fontsize=7, family=MONO, color=MUTED)
        ax.text(ia + 0.8, y - 0.17, f"{ia:.1f} min  (pré-alerte IA)", va="center", fontsize=7, family=MONO, color=ACC)
    ax.set_yticks(ys); ax.set_yticklabels([r[0] for r in rows], fontsize=7.6, color=INK)
    ax.set_xlim(0, 66); ax.set_xlabel("minutes depuis le début de la dérive", fontsize=7, color=MUTED)
    ax.tick_params(axis="x", labelsize=6.8, colors=MUTED, length=2); ax.tick_params(axis="y", length=0)
    for sp in ["top", "right", "left"]: ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(RULE_S); ax.spines["bottom"].set_linewidth(0.8)
    ax.grid(axis="x", color=RULE, linewidth=0.6); ax.set_axisbelow(True)
    fig.savefig(f"{OUT}/ia-avance.png", dpi=220, facecolor=BG)
    plt.close(fig)


if __name__ == "__main__":
    network(); wiring(); ia_lead()
    print("ok", OUT)
