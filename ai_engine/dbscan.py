import json
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.cluster import DBSCAN

# 1. JSON Verisini Okuma
ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / 'data' / 'oasis_db_output.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 2. Makro Kategori Belirleme (Sadece Altyapı ve Çevre için temel sözlük)
macro_mapping = {
    'road collapse': 'altyapi',
    'road_disruption': 'altyapi',
    'infrastructure failure': 'altyapi',
    'elektrik kesintisi': 'altyapi',
    'elektrik kazası': 'altyapi',
    'flood': 'cevre',
    'heyelan': 'cevre',
    'landslide': 'cevre',
    'fire': 'cevre'
}

parsed_data = []
for r in data['processed_reports']:
    lon, lat = r['gps_location']['coordinates']
    raw_cat = r['disaster_type'].lower().strip()
    
    # --- İNSAN KATEGORİSİ KONTROLÜ (ÖNCELİKLİ) ---
    indicators = r.get('indicators_json', {})
    user_status = r.get('user_status', '')
    
    is_human_risk = (
        indicators.get('people_trapped', False) or 
        indicators.get('people_injured', False) or 
        indicators.get('life_threat', False) or
        user_status == 'entrapment-life-threatening-risk'
    )
    
    # Eğer hayati tehlike varsa kategori direkt 'insan' olur.
    # Yoksa sözlükten eşleştiririz.
    if is_human_risk:
        macro_cat = 'insan'
    else:
        macro_cat = macro_mapping.get(raw_cat, 'diger')
        
    parsed_data.append({
        'id': r['id'], 
        'lat': lat, 
        'lon': lon, 
        'macro_category': macro_cat
    })

df = pd.DataFrame(parsed_data)

# 3. ADIM: Sadece Konum Bazlı DBSCAN (0.0005 epsilon)
spatial_dbscan = DBSCAN(eps=0.0005, min_samples=2)
df['spatial_cluster'] = spatial_dbscan.fit_predict(df[['lat', 'lon']])

# 4. ADIM: Konum ve Makro Kategoriyi Kesiştirme
final_clusters = []
cluster_map = {}
cluster_id_counter = 0

for idx, row in df.iterrows():
    s_id = row['spatial_cluster']
    m_cat = row['macro_category']
    
    if s_id == -1:
        # Tekil olaylar (Gürültü) -1 olarak kalır, kümeleşmez!
        final_clusters.append(-1)
    else:
        # Kesişim Anahtarı: (Mekansal Küme ID, Makro Kategori)
        merge_key = (s_id, m_cat)
        
        if merge_key not in cluster_map:
            cluster_map[merge_key] = cluster_id_counter
            cluster_id_counter += 1
            
        final_clusters.append(cluster_map[merge_key])

df['final_cluster'] = final_clusters

# 5. Görselleştirme (Sabit Renkli ve Temiz Lejant)
plt.figure(figsize=(12, 8))

# Kategori bazlı sabit renk ve Türkçe isim eşleştirmeleri
cat_style = {
    'insan':   {'color': 'red',    'label': 'İnsan'},
    'altyapi': {'color': 'orange', 'label': 'Altyapı'},
    'cevre':   {'color': 'blue',   'label': 'Çevre'},
    'diger':   {'color': 'gray',   'label': 'Diğer'}
}

# Çizim (Kategorilere göre gruplayıp çiziyoruz)
for cat in df['macro_category'].unique():
    cat_df = df[df['macro_category'] == cat]
    color = cat_style[cat]['color']
    label = cat_style[cat]['label']
    
    # Önce Tekil Olayları (x işaretiyle) çiz
    tekil_df = cat_df[cat_df['final_cluster'] == -1]
    if not tekil_df.empty:
        plt.scatter(tekil_df['lon'], tekil_df['lat'], 
                    color=color, marker='x', s=60, alpha=0.7, 
                    label=f"{label} (Tekil Olay)")
    
    # Sonra Kümeleri (büyük yuvarlaklarla) çiz
    kume_df = cat_df[cat_df['final_cluster'] != -1]
    if not kume_df.empty:
        plt.scatter(kume_df['lon'], kume_df['lat'], 
                    color=color, marker='o', s=250, edgecolors='black', alpha=0.9, 
                    label=f"{label} (Küme)")

# Lejantta aynı etiketin defalarca çıkmasını engelleyen kod
handles, labels = plt.gca().get_legend_handles_labels()
by_label = dict(zip(labels, handles))
plt.legend(by_label.values(), by_label.keys(), bbox_to_anchor=(1.05, 1), loc='upper left')

plt.title('Makro Kategorilere Göre Birleştirilmiş Afet Kümeleri')
plt.xlabel('Boylam (Longitude)')
plt.ylabel('Enlem (Latitude)')
plt.grid(True, linestyle='--', alpha=0.6)
plt.tight_layout()
plt.show()

# Özet Çıktı
print("Kümeleme Tamamlandı!")
print(f"Toplam nihai küme sayısı: {cluster_id_counter}")
print(f"Toplam tekil olay sayısı: {len(df[df['final_cluster'] == -1])}")