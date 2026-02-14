"""
DecarbIQ ERCOT Price Chord Diagram v2 — Detailed factor-level view.
Each individual driver is its own node; chords show regression-validated
causal pathways to electricity price via gas price.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.path import Path
from matplotlib.colors import to_rgba
import matplotlib.patheffects as pe
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Individual factor nodes ───────────────────────────────────────────────
# (name, color, angular_weight)
# Grouped by category, color-coded. Weight controls arc size.
NODES = [
    # --- ELECTRICITY PRICES (target) ---
    ("ERCOT\nWholesale",      "#d35400", 14),   # 0
    ("CAISO\nWholesale",      "#a04000", 10),   # 1

    # --- GENERATION MIX (TX) ---
    ("Gas 47%",               "#c0392b",  6),   # 2
    ("Wind 25%",              "#1abc9c",  6),   # 3
    ("Coal 16%",              "#7f8c8d",  5),   # 4
    ("Solar 8%",              "#f39c12",  4),   # 5

    # --- HENRY HUB GAS PRICE (central hub) ---
    ("Henry Hub\nGas Price",  "#e74c3c", 18),   # 6

    # --- SUPPLY ---
    ("Shale\nProduction",     "#2980b9",  5),   # 7
    ("LNG\nExports",          "#3498db",  4),   # 8
    ("Net\nExporter",         "#1a5276",  4),   # 9

    # --- MACRO DEMAND ---
    ("GDP\nGrowth",           "#27ae60",  7),   # 10
    ("Industrial\nProduction","#2ecc71",  5),   # 11
    ("Power Sector\nDemand",  "#1e8449",  5),   # 12
    ("Summer\nSeasonal",      "#82e0aa",  4),   # 13

    # --- DATA CENTERS (emerging) ---
    ("US Data\nCenter TWh",   "#e91e63",  6),   # 14
    ("TX DC Load\n55→200 TWh","#c2185b",  5),   # 15
    ("ERCOT Queue\n230+ GW",  "#880e4f",  5),   # 16

    # --- FEDERAL POLICY ---
    ("IRA→OBBBA",             "#8e44ad",  8),   # 17
    ("FERC\nReforms",         "#9b59b6",  5),   # 18
    ("MATS\nRule",            "#7d3c98",  4),   # 19

    # --- TX INFRASTRUCTURE ---
    ("TX CREZ\n$7B/18.5GW",  "#af7ac5",  4),   # 20

    # --- CA POLICY (CAISO-specific) ---
    ("CA RPS\n100% by 2045",  "#5b2c6f",  4),   # 21
    ("CA Cap &\nTrade",       "#6c3483",  4),   # 22

    # --- QUEUE CONSTRAINT ---
    ("Queue\n2,600 GW",       "#ff9800",  6),   # 23
    ("5yr Wait\n14% Complete", "#e65100", 4),   # 24
]

# ── Connections ───────────────────────────────────────────────────────────
# (from_idx, to_idx, magnitude, color, is_dominant)
# magnitude drives chord thickness
CONNECTIONS = [
    # === GAS PRICE → ELECTRICITY PRICES (the two big passthroughs) ===
    (6,  0,  41.0,  "#e74c3c", True),    # Gas → ERCOT: $41/MWh (p=0.004)
    (6,  1,  14.0,  "#a04000", True),    # Gas → CAISO: $14/MWh (p<0.001)

    # === GAS PRICE → GENERATION MIX (fuel cost drives dispatch) ===
    (6,  2,   8.0,  "#c0392b", False),   # Gas → Gas 47% share
    (6,  3,   3.0,  "#1abc9c", False),   # Gas → Wind (displaces when cheap)
    (6,  4,   2.5,  "#7f8c8d", False),   # Gas → Coal (fuel switching)

    # === GENERATION MIX → ERCOT ===
    (2,  0,   6.0,  "#c0392b", False),   # Gas 47% → ERCOT dispatch
    (3,  0,   4.0,  "#1abc9c", False),   # Wind 25% → ERCOT (compression)
    (4,  0,   2.0,  "#7f8c8d", False),   # Coal → ERCOT
    (5,  0,   1.5,  "#f39c12", False),   # Solar → ERCOT

    # === SUPPLY → GAS PRICE ===
    (7,  6,   5.0,  "#2980b9", False),   # Shale → Gas (-1.30)
    (8,  6,   2.0,  "#3498db", False),   # LNG → Gas (+0.12)
    (9,  6,   4.0,  "#1a5276", False),   # Net Exporter → Gas (-1.48)

    # === MACRO DEMAND → GAS PRICE (HH regression) ===
    (10, 6,   9.0,  "#27ae60", True),    # GDP → Gas (+0.486, p<0.001)
    (11, 6,   4.5,  "#2ecc71", False),   # Industrial → Gas (+0.118, p=0.005)
    (12, 6,   3.0,  "#1e8449", False),   # Power Sector → Gas (-0.064, p=0.043)
    (13, 6,   3.5,  "#82e0aa", False),   # Summer → Gas (+0.60, p=0.011)

    # === DATA CENTERS → GAS + ERCOT ===
    (14, 6,   4.0,  "#e91e63", False),   # US DC TWh → Gas (+0.059, p=0.002)
    (15, 0,   3.5,  "#c2185b", False),   # TX DC Load → ERCOT (demand pull)
    (16, 0,   3.0,  "#880e4f", False),   # ERCOT large load queue → ERCOT

    # === FEDERAL POLICY → GAS PRICE (HH regression) ===
    (17, 6,  18.0,  "#ff2222", True),    # IRA→OBBBA → Gas (+8.57, p<0.001)
    (18, 6,   4.5,  "#9b59b6", False),   # FERC Reforms → Gas (+0.57, p=0.023)
    (19, 6,   3.5,  "#7d3c98", False),   # MATS → Gas (+0.86)

    # === TX INFRASTRUCTURE → ERCOT ===
    (20, 0,   3.0,  "#af7ac5", False),   # TX CREZ → ERCOT (transmission)
    (20, 3,   2.5,  "#af7ac5", False),   # TX CREZ → Wind (enabled 40GW)

    # === CA POLICY → CAISO ===
    (21, 1,   4.5,  "#5b2c6f", False),   # CA RPS → CAISO
    (22, 1,   4.0,  "#6c3483", False),   # Cap & Trade → CAISO

    # === QUEUE → ERCOT ===
    (23, 0,   4.0,  "#ff9800", False),   # Queue backlog → ERCOT (delays)
    (24, 0,   2.5,  "#e65100", False),   # 5yr wait → ERCOT
    (23, 5,   2.0,  "#ff9800", False),   # Queue → Solar (blocks additions)

    # === DEMAND → ERCOT (seasonal load) ===
    (13, 0,   2.5,  "#82e0aa", False),   # Summer → ERCOT (peak load)
]

GAP_DEG = 1.8
OUTER_R = 1.0
ARC_WIDTH = 0.11
CHORD_SCALE = 0.0042
LABEL_R = 1.17


def _chord_path(ts1, te1, ts2, te2, r):
    """Quadratic Bezier chord between two arc segments."""
    n = 35
    t1 = np.linspace(ts1, te1, n)
    a1x, a1y = r * np.cos(t1), r * np.sin(t1)
    t2 = np.linspace(ts2, te2, n)
    a2x, a2y = r * np.cos(t2), r * np.sin(t2)

    verts, codes = [], []
    for i, (x, y) in enumerate(zip(a1x, a1y)):
        verts.append((x, y))
        codes.append(Path.MOVETO if i == 0 else Path.LINETO)

    verts.append((0, 0)); codes.append(Path.CURVE3)
    verts.append((a2x[0], a2y[0])); codes.append(Path.CURVE3)

    for x, y in zip(a2x, a2y):
        verts.append((x, y)); codes.append(Path.LINETO)

    verts.append((0, 0)); codes.append(Path.CURVE3)
    verts.append((a1x[0], a1y[0])); codes.append(Path.CURVE3)
    verts.append((a1x[0], a1y[0])); codes.append(Path.CLOSEPOLY)

    return Path(verts, codes)


def generate():
    fig, ax = plt.subplots(figsize=(20, 20), facecolor='#0f0f1e')
    ax.set_facecolor('#0f0f1e')
    ax.set_xlim(-1.65, 1.65)
    ax.set_ylim(-1.65, 1.65)
    ax.set_aspect('equal')
    ax.axis('off')

    # ── Compute arc angles ─────────────────────────────────────────────
    total_w = sum(w for _, _, w in NODES)
    nn = len(NODES)
    avail = 360.0 - nn * GAP_DEG

    arcs_deg, cur = [], 90.0
    for _, _, w in NODES:
        span = avail * (w / total_w)
        arcs_deg.append((cur, cur + span))
        cur += span + GAP_DEG
    arcs = [(np.radians(s), np.radians(e)) for s, e in arcs_deg]

    inner_r = OUTER_R - ARC_WIDTH

    # ── Category group labels (outer ring) ─────────────────────────────
    categories = [
        (0, 1,   "ELECTRICITY PRICES",  "#d35400"),
        (2, 5,   "GENERATION MIX",      "#c0392b"),
        (6, 6,   "GAS PRICE",           "#e74c3c"),
        (7, 9,   "SUPPLY",              "#2980b9"),
        (10, 13, "MACRO DEMAND",        "#27ae60"),
        (14, 16, "DATA CENTERS",        "#e91e63"),
        (17, 20, "POLICY",              "#8e44ad"),
        (21, 22, "CA POLICY",           "#5b2c6f"),
        (23, 24, "QUEUE",               "#ff9800"),
    ]

    for start_i, end_i, cat_name, cat_color in categories:
        theta_s = arcs[start_i][0]
        theta_e = arcs[end_i][1]
        theta_mid = (theta_s + theta_e) / 2
        cr = OUTER_R + 0.06
        cx, cy = cr * np.cos(theta_mid), cr * np.sin(theta_mid)

        angle = np.degrees(theta_mid)
        if 90 < angle % 360 < 270:
            rot = angle - 180
        else:
            rot = angle

        # Category arc line
        t_cat = np.linspace(theta_s, theta_e, 50)
        cat_r = OUTER_R + 0.025
        ax.plot(cat_r * np.cos(t_cat), cat_r * np.sin(t_cat),
                color=cat_color, linewidth=3, alpha=0.6, zorder=2,
                solid_capstyle='round')

    # ── Draw outer arcs ────────────────────────────────────────────────
    for i, (name, color, _) in enumerate(NODES):
        ts, te = arcs[i]
        np_pts = 60

        t_o = np.linspace(ts, te, np_pts)
        t_i = np.linspace(te, ts, np_pts)

        vx = np.concatenate([OUTER_R * np.cos(t_o), inner_r * np.cos(t_i),
                             [OUTER_R * np.cos(t_o[0])]])
        vy = np.concatenate([OUTER_R * np.sin(t_o), inner_r * np.sin(t_i),
                             [OUTER_R * np.sin(t_o[0])]])
        ax.fill(vx, vy, color=color, alpha=0.90, zorder=3)

        # Outer edge highlight
        ax.plot(OUTER_R * np.cos(t_o), OUTER_R * np.sin(t_o),
                color='white', linewidth=0.6, alpha=0.2, zorder=4)

        # End caps
        for t_cap in [ts, te]:
            ax.plot([inner_r * np.cos(t_cap), OUTER_R * np.cos(t_cap)],
                    [inner_r * np.sin(t_cap), OUTER_R * np.sin(t_cap)],
                    color='#0f0f1e', linewidth=1.2, zorder=4)

        # Tick marks
        n_ticks = max(3, int((te - ts) / 0.06))
        for tt in np.linspace(ts, te, n_ticks):
            tx1 = OUTER_R * np.cos(tt)
            ty1 = OUTER_R * np.sin(tt)
            tx2 = (OUTER_R + 0.012) * np.cos(tt)
            ty2 = (OUTER_R + 0.012) * np.sin(tt)
            ax.plot([tx1, tx2], [ty1, ty2], color=color,
                    linewidth=0.5, alpha=0.5, zorder=4)

    # ── Draw chords (sorted: small first, dominant on top) ─────────────
    arc_used = [0.0] * nn
    sorted_conns = sorted(CONNECTIONS, key=lambda c: c[2])

    for fi, ti, mag, color, dominant in sorted_conns:
        fs, fe = arcs[fi]
        ts, te = arcs[ti]
        f_span = fe - fs
        t_span = te - ts

        cw = mag * CHORD_SCALE
        wf = min(cw, f_span * 0.40)
        wt = min(cw, t_span * 0.40)

        cs1 = fs + arc_used[fi]
        ce1 = cs1 + wf
        cs2 = ts + arc_used[ti]
        ce2 = cs2 + wt

        if ce1 > fe:
            ce1 = fe; cs1 = ce1 - wf
        if ce2 > te:
            ce2 = te; cs2 = ce2 - wt

        arc_used[fi] += wf + 0.003
        arc_used[ti] += wt + 0.003

        path = _chord_path(cs1, ce1, cs2, ce2, inner_r)

        if dominant:
            alpha = 0.80
            edge_alpha = 0.5
            lw = 0.8
        elif mag > 8:
            alpha = 0.55
            edge_alpha = 0.3
            lw = 0.4
        elif mag > 4:
            alpha = 0.40
            edge_alpha = 0.2
            lw = 0.3
        else:
            alpha = 0.30
            edge_alpha = 0.15
            lw = 0.2

        patch = patches.PathPatch(
            path,
            facecolor=to_rgba(color, alpha=alpha),
            edgecolor=to_rgba(color, alpha=edge_alpha),
            linewidth=lw, zorder=2 if not dominant else 2.5
        )
        ax.add_patch(patch)

    # ── Labels ─────────────────────────────────────────────────────────
    for i, (name, color, _) in enumerate(NODES):
        mid = (arcs[i][0] + arcs[i][1]) / 2
        lx = LABEL_R * np.cos(mid)
        ly = LABEL_R * np.sin(mid)
        angle = np.degrees(mid)

        if 90 < angle % 360 < 270:
            rot = angle - 180
            ha = 'right'
        else:
            rot = angle
            ha = 'left'

        norm_a = angle % 360
        if (75 < norm_a < 105) or (255 < norm_a < 285):
            ha = 'center'

        # Larger font for key nodes
        fs = 11
        if i in (0, 1, 6):  # ERCOT, CAISO, Gas Price
            fs = 13

        ax.text(lx, ly, name, fontsize=fs, fontweight='bold',
                color=color, ha=ha, va='center',
                rotation=rot, rotation_mode='anchor',
                fontfamily='Arial', linespacing=0.85,
                path_effects=[pe.withStroke(linewidth=3, foreground='#0f0f1e')])

    # ── Center text ────────────────────────────────────────────────────
    # Dark circle in center for text
    circle_bg = plt.Circle((0, 0), 0.28, facecolor='#0f0f1e',
                           edgecolor='#333', linewidth=1, zorder=2.8)
    ax.add_patch(circle_bg)

    ax.text(0, 0.12, "DecarbIQ", fontsize=22, fontweight='bold',
            color='white', ha='center', va='center', fontfamily='Arial',
            path_effects=[pe.withStroke(linewidth=2, foreground='#0f0f1e')],
            zorder=3)
    ax.text(0, -0.01, "ERCOT Price", fontsize=12,
            color='#bbb', ha='center', va='center', fontfamily='Arial',
            zorder=3)
    ax.text(0, -0.12, "Relationship", fontsize=12,
            color='#bbb', ha='center', va='center', fontfamily='Arial',
            zorder=3)
    ax.text(0, -0.22, "Model v2.0", fontsize=10,
            color='#777', ha='center', va='center', fontfamily='Arial',
            zorder=3)

    # ── Legend (bottom-left) ───────────────────────────────────────────
    items = [
        ("DIRECT (ERCOT regression)", None, None, True),
        ("#e74c3c", "Gas Price: $41/MWh per $1 gas (p=0.004)", 5.0, False),
        ("", "", 0, False),
        ("INDIRECT VIA GAS (HH regression, R\u00b2=0.612)", None, None, True),
        ("#ff2222", "IRA to OBBBA: +$8.57/MMBtu (p<0.001)", 4.0, False),
        ("#27ae60", "GDP Growth: +$0.486/1% (p<0.001)", 2.5, False),
        ("#82e0aa", "Summer Seasonal: +$0.60 (p=0.011)", 1.8, False),
        ("#9b59b6", "FERC Reforms: +$0.57 (p=0.023)", 1.5, False),
        ("#2ecc71", "Industrial Prod: +$0.118 (p=0.005)", 1.3, False),
        ("#1e8449", "Power Sector: -$0.064 (p=0.043)", 1.0, False),
        ("#e91e63", "Data Center TWh: +$0.059 (p=0.002)", 1.0, False),
        ("", "", 0, False),
        ("STRUCTURAL / EMERGING", None, None, True),
        ("#c2185b", "TX Data Centers: 55 to 200 TWh by 2030", 1.3, False),
        ("#ff9800", "Queue Backlog: 2,600 GW, 5yr avg wait", 1.3, False),
        ("#1abc9c", "Wind: 25% TX share, price compression", 1.3, False),
        ("#af7ac5", "TX CREZ: $7B transmission, 18.5 GW", 1.0, False),
    ]

    lx0, ly0 = -1.60, -1.42
    row_h = 0.048
    total_h = len(items) * row_h + 0.06

    bg = plt.Rectangle((lx0 - 0.02, ly0 - 0.03), 1.38, total_h,
                        facecolor='#1a1a2e', edgecolor='#444',
                        linewidth=1, alpha=0.95, zorder=5)
    ax.add_patch(bg)

    ax.text(lx0 + 0.67, ly0 + total_h - 0.04,
            "ERCOT HIGH-IMPACT FACTORS",
            fontsize=11, fontweight='bold', color='white', ha='center',
            fontfamily='Arial', zorder=6)

    yy = ly0 + total_h - 0.09
    for item in items:
        if item[1] is None:
            # Section header
            ax.text(lx0 + 0.03, yy, item[0],
                    fontsize=8, fontweight='bold', color='#aaa',
                    fontfamily='Arial', zorder=6)
            yy -= row_h * 0.8
            continue
        if item[0] == "":
            yy -= row_h * 0.3
            continue
        lc, ltxt, lw, _ = item
        ax.plot([lx0 + 0.03, lx0 + 0.11], [yy, yy],
                color=lc, linewidth=lw, alpha=0.85,
                solid_capstyle='round', zorder=6)
        ax.text(lx0 + 0.14, yy, ltxt, fontsize=8, color='#ddd',
                va='center', fontfamily='Arial', zorder=6)
        yy -= row_h

    # ── Footer ─────────────────────────────────────────────────────────
    ax.text(0, -1.58,
            "HH Full R\u00b2=0.612 (n=336)  |  ERCOT Full R\u00b2=0.149 (n=169)  |  CAISO Full R\u00b2=0.569 (n=183)",
            fontsize=9, color='#888', ha='center', fontfamily='Arial')
    ax.text(0, -1.53,
            "Chord thickness ~ regression coefficient  |  *** p<0.001  ** p<0.01  * p<0.05",
            fontsize=8, color='#666', ha='center', fontfamily='Arial')

    # ── Save ───────────────────────────────────────────────────────────
    png = os.path.join(OUTPUT_DIR, "decarbiq_relationship_chord.png")
    svg = os.path.join(OUTPUT_DIR, "decarbiq_relationship_chord.svg")
    fig.savefig(png, dpi=200, bbox_inches='tight',
                facecolor='#0f0f1e', edgecolor='none')
    fig.savefig(svg, bbox_inches='tight',
                facecolor='#0f0f1e', edgecolor='none')
    plt.close(fig)
    print(f"Saved: {png}")
    print(f"Saved: {svg}")


if __name__ == "__main__":
    generate()
