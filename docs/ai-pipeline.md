KULLANICI
    │
    ▼
┌─ raw_reports ─────────────────────────────────┐
│  GPS: (lat, lon)                               │
│  event_define: metin                           │
│  image: fotoğraf                               │
└────────────────────────────────────────────────┘
    │
    ├─► image_service.py
    │   ├─ dms_to_decimal()      → EXIF GPS
    │   ├─ thumbnail(1920)       → sıkıştırma
    │   └─► exif_file INSERT
    │
    ├─► ai_service.py
    │   ├─ _call_gemini(LLM)     → llm_indicators{18 boolean}
    │   ├─ _call_gemini(VLM)     → vlm_indicators{18 boolean}
    │   │
    │   └─► indicator_scoring.py
    │       ├─ calculate_severity()
    │       │   ├─ merge(llm ∪ vlm)
    │       │   ├─ Σ(points) per category
    │       │   ├─ min(sum, cap) per category
    │       │   ├─ severity = Σ capped categories
    │       │   └─ reliability = 0.5 + 0.5×(cross/total)
    │       │
    │       ├─ determine_user_status()
    │       │   └─ priority-based rule chain
    │       │
    │       └─ recommend_action()
    │           └─ severity + status → action
    │
    │   └─► processed_reports INSERT
    │       ├─ severity_score: 1.0–10.0
    │       ├─ reliability_score: 0.3–1.0
    │       ├─ user_status: enum
    │       ├─ recommended_action: enum
    │       └─ indicators_json: {tüm detay}
    │
    └─► cluster_service.py
        ├─ ST_DWithin()          → yakın cluster bul
        ├─ ST_Centroid()         → merkez güncelle
        ├─ MAX(ST_Distance())    → radius güncelle
        └─ AVG(severity)         → ortalama güncelle
        └─► disaster_clusters UPDATE

            │
            ▼
        DASHBOARD
        ├─ impact_radius = 50 + severity×20
        ├─ color = severity → kırmızı/sarı/beyaz
        ├─ grid = metre → derece dönüşümü
        └─ reliability = ● ◐ ○ sembol