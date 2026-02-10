#!/usr/bin/env python3
"""
DecarbIQ ERCOT Energy Price DAG — Chord Diagram
Line thickness ∝ |coefficient|
Filtered to top 50% impact nodes.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge
import matplotlib.patches as mpatches
from collections import defaultdict, OrderedDict
from decarbiq_graph import build_ercot_graph

# ── Style Config ─────────────────────────────────────────────────────────────

TYPE_STYLE = OrderedDict([
    ('upstream_supply',        {'color': '#D35400', 'label': 'Upstream Supply'}),
    ('upstream_demand',        {'color': '#E67E22', 'label': 'Upstream Demand'}),
    ('upstream_storage',       {'color': '#F1C40F', 'label': 'Storage'}),
    ('derived',                {'color': '#3498DB', 'label': 'Derived Variables'}),
    ('seasonal',               {'color': '#27AE60', 'label': 'Seasonal'}),
    ('moderator',              {'color': '#16A085', 'label': 'Generation Mix (Moderator)'}),
    ('structural_shifter',     {'color': '#8E44AD', 'label': 'Policy / Structural Shift'}),
    ('disruption_weather',     {'color': '#5DADE2', 'label': 'Weather Disruption'}),
    ('disruption_geopolitical', {'color': '#AF7AC5', 'label': 'Geopolitical Disruption'}),
    ('disruption_market',      {'color': '#45B39D', 'label': 'Market Disruption'}),
    ('gas_price',              {'color': '#C0392B', 'label': 'Henry Hub Gas Price'}),
    ('elec_wholesale',         {'color': '#E74C3C', 'label': 'ERCOT Wholesale Electricity'}),
    ('elec_retail',            {'color': '#A93226', 'label': 'TX Industrial Retail'}),
])

BG_COLOR = '#FAFAFA'


# ── Helpers ──────────────────────────────────────────────────────────────────

def bezier_points(theta1, theta2, r=0.92, n_pts=120):
    """Quadratic Bézier chord between two angles on the circle."""
    p0 = np.array([r * np.cos(theta1), r * np.sin(theta1)])
    p2 = np.array([r * np.cos(theta2), r * np.sin(theta2)])

    mid = (p0 + p2) / 2.0
    dist_norm = np.linalg.norm(p2 - p0) / (2 * r)
    pull = 0.15 + 0.70 * dist_norm
    ctrl = mid * (1 - pull)

    t = np.linspace(0, 1, n_pts).reshape(-1, 1)
    pts = (1 - t)**2 * p0 + 2 * (1 - t) * t * ctrl + t**2 * p2
    return pts[:, 0], pts[:, 1]


def short_label(node_id, node_type, full_label):
    """Concise label for the outer ring."""
    if node_type == 'structural_shifter':
        s = node_id.replace('post_', '').replace('_', ' ')
        return s.title() if len(s) <= 22 else s[:19].title() + '...'
    if len(full_label) > 30:
        return full_label[:27] + '...'
    return full_label


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    g = build_ercot_graph()

    # ── 1. Collect ALL computational edges ───
    all_edges = []
    for e in g.edges.values():
        if e.edge_type in ('causal', 'structural_shift', 'moderating'):
            all_edges.append({
                'src': e.source,
                'tgt': e.target,
                'w':   abs(e.coefficient),
                'coeff': e.coefficient,
                'etype': e.edge_type,
            })

    # ── 2. Compute max |coeff| per node (causal + structural only) ───
    node_max_coeff = defaultdict(float)
    for e in all_edges:
        if e['etype'] in ('causal', 'structural_shift'):
            node_max_coeff[e['src']] = max(node_max_coeff[e['src']], e['w'])
            node_max_coeff[e['tgt']] = max(node_max_coeff[e['tgt']], e['w'])

    # ── 3. Top-50% filter: keep nodes above the median coefficient ───
    vals = sorted(node_max_coeff.values())
    median_val = vals[len(vals) // 2] if vals else 0

    keep = {nid for nid, v in node_max_coeff.items() if v >= median_val}

    # Always keep the three hub/output nodes
    keep.update(['henry_hub', 'ercot_wholesale', 'tx_retail'])

    # Keep moderator source nodes (gas_share_pct) if they have moderating edges
    for e in all_edges:
        if e['etype'] == 'moderating' and e['src'] in node_max_coeff:
            keep.add(e['src'])

    # ── 4. Filter edges to only kept nodes ───
    edges = [e for e in all_edges
             if e['src'] in keep and e['tgt'] in keep]

    # ── 5. Build ordered node list ───
    ordered = []
    for ntype in TYPE_STYLE:
        nds = sorted(
            [n for n in g.nodes.values()
             if n.node_type == ntype and n.node_id in keep],
            key=lambda x: x.node_id,
        )
        ordered.extend(nds)

    N = len(ordered)
    ids = [n.node_id for n in ordered]
    idx = {nid: i for i, nid in enumerate(ids)}
    types = {n.node_id: n.node_type for n in ordered}

    # ── 6. Node arc sizing (role-based) ───
    arc_weight = {}
    for nid in ids:
        if types[nid] in ('gas_price', 'elec_wholesale', 'elec_retail'):
            arc_weight[nid] = 5.0
        elif types[nid] == 'structural_shifter':
            arc_weight[nid] = 0.8
        elif types[nid] in ('moderator',):
            arc_weight[nid] = 1.5
        elif types[nid] in ('derived',):
            arc_weight[nid] = 1.0
        else:
            arc_weight[nid] = 1.2

    total_w = sum(arc_weight[nid] for nid in ids)

    # ── 7. Assign arc angles ───
    n_groups = 0
    prev = None
    for n in ordered:
        if n.node_type != prev:
            n_groups += 1
            prev = n.node_type

    gap = 0.035 * 2 * np.pi
    avail = 2 * np.pi - n_groups * gap

    angles = {}
    theta = np.pi / 2
    prev_t = None

    for n in ordered:
        if n.node_type != prev_t:
            if prev_t is not None:
                theta += gap
            prev_t = n.node_type
        arc = (arc_weight[n.node_id] / total_w) * avail
        arc = max(arc, 0.018)
        angles[n.node_id] = (theta, theta + arc, theta + arc / 2)
        theta += arc

    # ── 8. DRAW ───
    fig, ax = plt.subplots(figsize=(20, 20), facecolor=BG_COLOR)
    ax.set_xlim(-1.70, 1.70)
    ax.set_ylim(-1.70, 1.70)
    ax.set_aspect('equal')
    ax.axis('off')

    R_out   = 1.00
    R_in    = 0.92
    R_chord = 0.915
    R_lbl   = 1.09

    # ── 8a. Outer arcs ───
    for nid in ids:
        s, e, _ = angles[nid]
        color = TYPE_STYLE[types[nid]]['color']
        w = Wedge((0, 0), R_out, np.degrees(s), np.degrees(e),
                  width=R_out - R_in,
                  fc=color, ec='white', lw=0.8, alpha=0.92, zorder=3)
        ax.add_patch(w)

    # ── 8b. Chords (thin first → thick on top) ───
    for e in sorted(edges, key=lambda x: x['w']):
        if e['src'] not in idx or e['tgt'] not in idx:
            continue

        _, _, t1 = angles[e['src']]
        _, _, t2 = angles[e['tgt']]
        bx, by = bezier_points(t1, t2, R_chord)

        lw = 0.4 + 4.0 * np.sqrt(e['w'])
        lw = min(lw, 15.0)

        color = TYPE_STYLE[types[e['src']]]['color']
        alpha = 0.12 + 0.35 * min(e['w'] / 3.0, 1.0)

        if e['etype'] == 'moderating':
            ax.plot(bx, by, lw=lw * 0.8, color='#16A085', alpha=0.55,
                    ls=':', zorder=2)
        else:
            ax.plot(bx, by, lw=lw, color=color, alpha=alpha,
                    solid_capstyle='round', zorder=2)

    # ── 8c. Annotate key downstream edges ───
    for key_edge in edges:
        if key_edge['src'] == 'henry_hub' and key_edge['tgt'] == 'ercot_wholesale':
            _, _, t1 = angles['henry_hub']
            _, _, t2 = angles['ercot_wholesale']
            mid_t = (t1 + t2) / 2
            ann_r = R_chord * 0.48
            ax.annotate(
                f"$\\beta$ = {key_edge['coeff']:.1f} $/MWh\nper $/MMBtu",
                xy=(ann_r * np.cos(mid_t), ann_r * np.sin(mid_t)),
                fontsize=9, color='#C0392B', fontweight='bold',
                ha='center', va='center',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#C0392B',
                          alpha=0.92, lw=1.3),
                zorder=5,
            )
        if key_edge['src'] == 'henry_hub' and key_edge['tgt'] == 'tx_retail':
            _, _, t1 = angles['henry_hub']
            _, _, t2 = angles['tx_retail']
            mid_t = (t1 + t2) / 2
            ann_r = R_chord * 0.35
            ax.annotate(
                f"$\\beta$ = {key_edge['coeff']:.1f} $/MWh\nper $/MMBtu",
                xy=(ann_r * np.cos(mid_t), ann_r * np.sin(mid_t)),
                fontsize=8, color='#A93226', fontweight='bold',
                ha='center', va='center',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#A93226',
                          alpha=0.92, lw=1.0),
                zorder=5,
            )

    # ── 8d. Node labels ───
    for nid in ids:
        _, _, ang = angles[nid]
        x = R_lbl * np.cos(ang)
        y = R_lbl * np.sin(ang)

        lbl = short_label(nid, types[nid], g.nodes[nid].label)
        is_key = types[nid] in ('gas_price', 'elec_wholesale', 'elec_retail')

        if is_key:
            fs, fw = 11.0, 'bold'
        elif types[nid] == 'structural_shifter':
            fs, fw = 7.0, 'normal'
        elif types[nid] in ('moderator', 'derived'):
            fs, fw = 8.0, 'normal'
        else:
            fs, fw = 8.5, 'normal'

        rot = np.degrees(ang) % 360
        ha = 'left'
        if 90 < rot < 270:
            rot += 180
            ha = 'right'

        ax.text(x, y, lbl, fontsize=fs, ha=ha, va='center',
                rotation=rot, rotation_mode='anchor',
                color='#2C3E50', fontweight=fw)

    # ── 8e. Legend (bottom-right corner) ───
    handles = []
    for ntype, cfg in TYPE_STYLE.items():
        if any(types.get(nid) == ntype for nid in ids):
            handles.append(
                mpatches.Patch(color=cfg['color'], alpha=0.9, label=cfg['label'])
            )

    leg = ax.legend(
        handles=handles, fontsize=10,
        framealpha=0.96, title='Node Categories', title_fontsize=11.5,
        bbox_to_anchor=(1.0, 0.0),
        loc='lower right',
        borderpad=1.0,
    )
    leg.get_frame().set_edgecolor('#BDC3C7')
    leg.get_frame().set_linewidth(1.5)

    # Title (top center, outside the ring)
    ax.text(0, 1.55, 'DecarbIQ  —  ERCOT Energy Price DAG',
            fontsize=18, ha='center', va='center',
            fontweight='bold', color='#2C3E50', zorder=10)
    ax.text(0, 1.43,
            f'Top 50% impact nodes  ·  {N} of {len(g.nodes)} nodes  ·  '
            f'{len(edges)} edges  ·  '
            f'cutoff |coeff| $\\geq$ {median_val:.2f}',
            fontsize=10.5, ha='center', va='center', color='#7F8C8D')
    ax.text(0, 1.34, 'Line thickness $\\propto$ |coefficient|   ·   Color = source node type',
            fontsize=9.5, ha='center', va='center', color='#95A5A6', style='italic')

    # ── 9. Save ───
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'ercot_chord_diagram.png')
    fig.savefig(out, dpi=180, bbox_inches='tight', facecolor=BG_COLOR)
    plt.close()
    print(f"Saved: {out}")
    print(f"  Nodes shown: {N} / {len(g.nodes)}")
    print(f"  Edges shown: {len(edges)}")
    print(f"  Median |coeff| cutoff: {median_val:.3f}")
    print(f"  Nodes kept: {sorted(ids)}")


if __name__ == '__main__':
    main()
