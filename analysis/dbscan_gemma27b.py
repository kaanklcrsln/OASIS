import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.cluster import DBSCAN

# 1. Load processed_reports from API dump
#    curl -s http://localhost:8000/api/reports/ > data/gemma_reports.json
#    python analysis/dbscan_gemma27b.py [path/to/dump.json]
import sys
from pathlib import Path
_dump = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'data' / 'gemma_reports.json'
with open(_dump, 'r', encoding='utf-8') as f:
    data = json.load(f)

# 2. Parse — only processed reports
parsed_data = []
for r in data['reports']:
    if r.get('severity_score') is None or not r.get('processed_disaster_type'):
        continue

    lat = r.get('latitude', 0)
    lon = r.get('longitude', 0)
    if lat == 0 and lon == 0:
        continue

    raw_cat = (r.get('processed_disaster_type') or 'diğer').lower().strip()
    indicators = r.get('indicators') or {}
    llm_ind = indicators.get('llm') or {}
    active = r.get('active_indicators') or []
    user_status = r.get('processed_user_status') or ''
    severity = r.get('severity_score') or 1.0

    is_human_risk = (
        llm_ind.get('people_trapped', False) or
        llm_ind.get('people_injured', False) or
        llm_ind.get('life_threat', False) or
        'people_trapped' in active or
        'life_threat' in active or
        'people_injured' in active
    )

    macro_mapping = {
        'sel': 'environment',
        'heyelan': 'environment',
        'yangın': 'environment',
        'gaz sızıntısı': 'infrastructure',
        'elektrik kesintisi': 'infrastructure',
        'elektrik': 'infrastructure',
        'yapı çöküşü': 'infrastructure',
        'deprem': 'environment',
        'tıbbi acil': 'other',
        'sağlık': 'other',
        'fırtına': 'environment',
        'kar': 'environment',
        'belirsiz': 'other',
        'bilinmiyor': 'other',
    }

    macro_cat = 'human' if is_human_risk else macro_mapping.get(raw_cat, 'other')

    parsed_data.append({
        'id': r['report_id'],
        'lat': lat,
        'lon': lon,
        'macro_category': macro_cat,
        'raw_category': raw_cat,
        'severity': severity,
        'user_status': user_status,
    })

df = pd.DataFrame(parsed_data)
print(f"Toplam rapor: {len(df)}")
print("Kategori dağılımı:")
print(df['macro_category'].value_counts())

# 3. Category-Aware DBSCAN
df['final_cluster'] = -1
global_cluster_id = 0

EPS = 0.0005        # ~50m
MIN_SAMPLES = 2

for cat_name, group_df in df.groupby('macro_category'):
    if len(group_df) < MIN_SAMPLES:
        continue
    dbscan = DBSCAN(eps=EPS, min_samples=MIN_SAMPLES)
    local_clusters = dbscan.fit_predict(group_df[['lat', 'lon']])
    cluster_mapping = {
        loc_id: (global_cluster_id + i)
        for i, loc_id in enumerate(sorted(set(local_clusters) - {-1}))
    }
    global_cluster_id += len(cluster_mapping)
    for idx, loc_id in zip(group_df.index, local_clusters):
        if loc_id != -1:
            df.at[idx, 'final_cluster'] = cluster_mapping[loc_id]

n_clusters = global_cluster_id
n_noise = len(df[df['final_cluster'] == -1])
print(f"\nKüme sayısı: {n_clusters}")
print(f"Tekil olay (gürültü): {n_noise}")

# 4. Visualization
BG = 'white'
GRID_C = '#d1d5db'
SPINE_C = '#6b7280'
TEXT_C = '#111827'

cat_style = {
    'human':          {'color': '#dc2626', 'label': 'İnsan Riski'},
    'infrastructure': {'color': '#ea580c', 'label': 'Altyapı'},
    'environment':    {'color': '#2563eb', 'label': 'Çevre / Doğal Afet'},
    'other':          {'color': '#6b7280', 'label': 'Diğer / Belirsiz'},
}

fig, axes = plt.subplots(1, 2, figsize=(18, 8))
fig.patch.set_facecolor(BG)

# ── Sol: DBSCAN kümeleme haritası ──
ax = axes[0]
ax.set_facecolor('#f9fafb')

for cat in df['macro_category'].unique():
    style = cat_style.get(cat, cat_style['other'])
    cat_df = df[df['macro_category'] == cat]
    color = style['color']
    label = style['label']

    isolated = cat_df[cat_df['final_cluster'] == -1]
    if not isolated.empty:
        ax.scatter(
            isolated['lon'], isolated['lat'],
            color=color, marker='x', s=70, linewidths=1.6,
            alpha=0.65, zorder=3, label=f'{label} — Tekil',
        )

    clustered = cat_df[cat_df['final_cluster'] != -1]
    if not clustered.empty:
        ax.scatter(
            clustered['lon'], clustered['lat'],
            color=color, marker='o', s=220,
            edgecolors='white', linewidths=0.9,
            alpha=0.92, zorder=4, label=f'{label} — Küme',
        )
        for cid, grp in clustered.groupby('final_cluster'):
            cx, cy = grp['lon'].mean(), grp['lat'].mean()
            ax.text(cx, cy, str(int(cid)),
                    fontsize=7, fontweight='bold',
                    color='white', ha='center', va='center', zorder=5)

ax.grid(True, linestyle='--', linewidth=0.5, color=GRID_C, alpha=1.0)
for spine in ax.spines.values():
    spine.set_edgecolor(SPINE_C)
    spine.set_linewidth(0.8)
ax.set_xlabel('Boylam', fontsize=11, color=TEXT_C, labelpad=8)
ax.set_ylabel('Enlem', fontsize=11, color=TEXT_C, labelpad=8)
ax.tick_params(colors=TEXT_C, labelsize=8)
ax.set_title('DBSCAN Kümeleme — Kategoriye Göre', fontsize=13, fontweight='bold', color=TEXT_C, pad=14)

legend = ax.legend(
    loc='upper left', bbox_to_anchor=(0, -0.12), borderaxespad=0,
    framealpha=1.0, edgecolor=SPINE_C, facecolor='white',
    labelcolor=TEXT_C, fontsize=9, ncol=2,
    handlelength=1.8, handleheight=1.2, labelspacing=0.6,
)
legend.get_frame().set_linewidth(0.8)

# ── Sağ: Severity dağılımı ──
ax2 = axes[1]
ax2.set_facecolor('#f9fafb')

# Scatter: her nokta severity ile boyutlandırılmış
sev_colors = []
for _, row in df.iterrows():
    s = row['severity']
    if s >= 7:
        sev_colors.append('#dc2626')
    elif s >= 4:
        sev_colors.append('#f97316')
    else:
        sev_colors.append('#3b82f6')

sizes = (df['severity'] ** 1.6) * 12

ax2.scatter(
    df['lon'], df['lat'],
    c=sev_colors, s=sizes,
    edgecolors='white', linewidths=0.7,
    alpha=0.85, zorder=4,
)

# Yüksek severity etiketle
high_sev = df[df['severity'] >= 7.0]
for _, row in high_sev.iterrows():
    ax2.annotate(
        f"{row['severity']:.1f}",
        (row['lon'], row['lat']),
        xytext=(4, 4), textcoords='offset points',
        fontsize=7, color='#dc2626', fontweight='bold',
    )

ax2.grid(True, linestyle='--', linewidth=0.5, color=GRID_C, alpha=1.0)
for spine in ax2.spines.values():
    spine.set_edgecolor(SPINE_C)
    spine.set_linewidth(0.8)
ax2.set_xlabel('Boylam', fontsize=11, color=TEXT_C, labelpad=8)
ax2.set_ylabel('Enlem', fontsize=11, color=TEXT_C, labelpad=8)
ax2.tick_params(colors=TEXT_C, labelsize=8)
ax2.set_title('Severity Score Dağılımı', fontsize=13, fontweight='bold', color=TEXT_C, pad=14)

from matplotlib.lines import Line2D
sev_legend = [
    Line2D([0],[0], marker='o', color='w', markerfacecolor='#dc2626', markersize=10, label='Yüksek Risk (7–10)'),
    Line2D([0],[0], marker='o', color='w', markerfacecolor='#f97316', markersize=8,  label='Orta Risk (4–6)'),
    Line2D([0],[0], marker='o', color='w', markerfacecolor='#3b82f6', markersize=6,  label='Düşük Risk (1–3)'),
]
ax2.legend(handles=sev_legend, loc='upper left', bbox_to_anchor=(0, -0.12),
           framealpha=1.0, edgecolor=SPINE_C, facecolor='white',
           labelcolor=TEXT_C, fontsize=9, ncol=3,
           handlelength=1.8, labelspacing=0.6)

# Genel başlık
fig.suptitle(
    'Gemma 3 27B — Processed Reports DBSCAN Analizi  |  Ordu / Altınordu / Akyazı',
    fontsize=14, fontweight='bold', color=TEXT_C, y=1.01,
)
fig.text(
    0.5, 0.97,
    f'ε = {EPS}°  ·  min_samples = {MIN_SAMPLES}  ·  '
    f'Toplam küme: {n_clusters}  ·  Tekil olay: {n_noise}  ·  '
    f'Toplam rapor: {len(df)}',
    ha='center', fontsize=9, color='#6b7280',
)

plt.tight_layout(rect=[0, 0.06, 1, 0.97])
import os
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs', 'dbscan_gemma27b_result.png')
plt.savefig(out_path, dpi=180, bbox_inches='tight', facecolor=BG)
print(f"\nGrafik kaydedildi: {out_path}")
plt.show()

# 5. Summary
print("\n=== ÖZET ===")
print(f"Küme sayısı      : {n_clusters}")
print(f"Tekil olay       : {n_noise}")
print(f"Toplam rapor     : {len(df)}")
print(f"Avg severity     : {df['severity'].mean():.2f}")
print(f"Yüksek risk (≥7) : {len(df[df['severity'] >= 7])}")
print(f"Orta risk (4-6)  : {len(df[(df['severity'] >= 4) & (df['severity'] < 7)])}")
print(f"Düşük risk (<4)  : {len(df[df['severity'] < 4])}")
print("\nKüme başına rapor dağılımı:")
for cid, grp in df[df['final_cluster'] != -1].groupby('final_cluster'):
    cats = grp['raw_category'].value_counts().to_dict()
    print(f"  Küme {int(cid):2d}: {len(grp)} rapor | {cats}")
