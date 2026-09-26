import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from sklearn.cluster import DBSCAN

# 1. Load JSON Data
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / 'data' / 'oasis_db_output.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 2. Define Macro Categories (base mapping for Infrastructure and Environment)
macro_mapping = {
    'road collapse':          'infrastructure',
    'road_disruption':        'infrastructure',
    'infrastructure failure': 'infrastructure',
    'elektrik kesintisi':     'infrastructure',
    'elektrik kazasi':        'infrastructure',
    'flood':                  'environment',
    'heyelan':                'environment',
    'landslide':              'environment',
    'fire':                   'environment',
}

parsed_data = []
for r in data['processed_reports']:
    lon, lat = r['gps_location']['coordinates']
    # Use raw_cat instead of macro_category for disaster-type-specific clustering
    raw_cat = r['disaster_type'].lower().strip()

    # --- HUMAN RISK CHECK (PRIORITY) ---
    indicators  = r.get('indicators_json', {})
    user_status = r.get('user_status', '')

    is_human_risk = (
        indicators.get('people_trapped', False) or
        indicators.get('people_injured', False) or
        indicators.get('life_threat',    False) or
        user_status == 'entrapment-life-threatening-risk'
    )

    macro_cat = 'human' if is_human_risk else macro_mapping.get(raw_cat, 'other')

    parsed_data.append({
        'id':            r['id'],
        'lat':           lat,
        'lon':           lon,
        'macro_category': macro_cat,
        'raw_category':  raw_cat,  # kept for reference
    })

df = pd.DataFrame(parsed_data)

# 3 & 4. Category-Aware Independent DBSCAN (spatial + category)
# Replace 'macro_category' with 'raw_category' for fine-grained type clustering.
df['final_cluster'] = -1
global_cluster_id   = 0

for cat_name, group_df in df.groupby('macro_category'):
    if len(group_df) < 2:   # need at least min_samples points to form a cluster
        continue

    dbscan        = DBSCAN(eps=0.0005, min_samples=2)
    local_clusters = dbscan.fit_predict(group_df[['lat', 'lon']])

    # Map local IDs (0, 1, 2…) to globally unique cluster IDs
    cluster_mapping = {
        loc_id: (global_cluster_id + i)
        for i, loc_id in enumerate(sorted(set(local_clusters) - {-1}))
    }
    global_cluster_id += len(cluster_mapping)

    for idx, loc_id in zip(group_df.index, local_clusters):
        if loc_id != -1:
            df.at[idx, 'final_cluster'] = cluster_mapping[loc_id]

# ── 5. Academic Visualization ─────────────────────────────────────────────────
BG      = 'white'
GRID_C  = '#d1d5db'   # light gray grid
SPINE_C = '#6b7280'   # medium gray spines
TEXT_C  = '#111827'   # near-black text

cat_style = {
    'human':          {'color': '#dc2626', 'label': 'İnsan Riski'},
    'infrastructure': {'color': '#ea580c', 'label': 'Altyapı'},
    'environment':    {'color': '#2563eb', 'label': 'Çevre'},
    'other':          {'color': '#6b7280', 'label': 'Diğer'},
}

fig, ax = plt.subplots(figsize=(13, 8))
fig.patch.set_facecolor(BG)
ax.set_facecolor('#f9fafb')   # very light gray plot area

for cat in df['macro_category'].unique():
    style   = cat_style.get(cat, cat_style['other'])
    cat_df  = df[df['macro_category'] == cat]
    color   = style['color']
    label   = style['label']

    # Isolated events
    isolated = cat_df[cat_df['final_cluster'] == -1]
    if not isolated.empty:
        ax.scatter(
            isolated['lon'], isolated['lat'],
            color=color, marker='x', s=70, linewidths=1.6,
            alpha=0.70, zorder=3, label=f'{label} — Tekil Olay',
        )

    # Clustered events
    clustered = cat_df[cat_df['final_cluster'] != -1]
    if not clustered.empty:
        ax.scatter(
            clustered['lon'], clustered['lat'],
            color=color, marker='o', s=240,
            edgecolors='white', linewidths=0.9,
            alpha=0.92, zorder=4, label=f'{label} — Küme',
        )

        # Annotate cluster IDs
        for cid, grp in clustered.groupby('final_cluster'):
            cx, cy = grp['lon'].mean(), grp['lat'].mean()
            ax.text(
                cx, cy, str(int(cid)),
                fontsize=7, fontweight='bold',
                color='white', ha='center', va='center', zorder=5,
            )

# Grid & spines
ax.grid(True, linestyle='--', linewidth=0.5, color=GRID_C, alpha=1.0)
for spine in ax.spines.values():
    spine.set_edgecolor(SPINE_C)
    spine.set_linewidth(0.8)

# Axis labels & ticks
ax.set_xlabel('Boylam (Longitude)', fontsize=12, color=TEXT_C, labelpad=8)
ax.set_ylabel('Enlem (Latitude)',   fontsize=12, color=TEXT_C, labelpad=8)
ax.tick_params(colors=TEXT_C, labelsize=9)
for label_obj in ax.get_xticklabels() + ax.get_yticklabels():
    label_obj.set_color(TEXT_C)

# Title
ax.set_title(
    'Kategoriye Özgü Afet Olay Kümeleme  —  DBSCAN Konumsal Analizi',
    fontsize=14, fontweight='bold', color=TEXT_C, pad=22,
)

# Subtitle annotation
fig.text(
    0.5, 0.94,
    f'ε = 0.0005°  ·  min_samples = 2  ·  Toplam küme: {global_cluster_id}  ·  '
    f'Tekil olay: {len(df[df["final_cluster"] == -1])}',
    ha='center', fontsize=9, color='#6b7280',
)

# Legend
legend = ax.legend(
    loc='upper left', bbox_to_anchor=(1.02, 1), borderaxespad=0,
    framealpha=1.0, edgecolor=SPINE_C, facecolor='white',
    labelcolor=TEXT_C, fontsize=11, title='Kategori',
    title_fontsize=11, handlelength=2.0, handleheight=1.4,
    labelspacing=0.8, borderpad=1.0, handletextpad=0.8,
)
legend.get_title().set_color(TEXT_C)

plt.tight_layout(rect=[0, 0, 1, 0.91])
plt.savefig(ROOT / 'images' / 'dbscan_result_academic.png', dpi=180, bbox_inches='tight', facecolor=BG)
plt.show()

# ── Summary ───────────────────────────────────────────────────────────────────
print("Kümeleme tamamlandı.")
print(f"  Toplam küme   : {global_cluster_id}")
print(f"  Isolated events: {len(df[df['final_cluster'] == -1])}")
