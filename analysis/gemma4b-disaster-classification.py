"""
Gemma 3 27B — Afet Türü Sınıflandırma Başarı Analizi
======================================================
Amaç:
    50 raporun tamamını Gemma 3 27B LLM ile analiz eder.
    Gösterge tespiti, afet türü sınıflandırması ve aciliyet puanlamasındaki
    başarıyı beklenen (ground truth) değerlerle karşılaştırarak ölçer.

    multi-model-consensus.py ile birebir aynı rapor seti ve ground truth
    kullanılır; fark olarak sınıflandırma spaCy anahtar kelime eşleşmesi
    yerine Gemma 3 27B LLM tarafından yapılır.

Bağımlılıklar:
    pip install google-genai python-dotenv pandas matplotlib seaborn numpy

Çalıştırma:
    cd analysis && python gemma4b-disaster-classification.py

Çıktılar (analysis/outputs/):
    - gemma27b_indicator_heatmap.png          : 50×18 gösterge tespit ısı haritası
    - gemma27b_disaster_classification.png    : Confusion matrix + kategori başarı çubuğu
    - gemma27b_indicator_frequency.png        : Gösterge tespit frekansları
    - gemma27b_severity_analysis.png          : Aciliyet skoru dağılımı ve kutu grafiği
    - gemma27b_summary.csv                    : Tam sonuç tablosu

Cache:
    .cache/gemma27b_disaster_predictions.json — API yanıtları önbelleğe alınır;
    tekrar çalıştırmada API çağrısı yapılmaz.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from dotenv import load_dotenv

# ── Dizin ayarları ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_BACKUP")
if not API_KEY:
    sys.exit("❌  GEMINI_API_KEY bulunamadı. .env dosyasını kontrol edin.")

OUTPUT_DIR = Path(__file__).parent / "outputs"
CACHE_DIR  = Path(__file__).parent / ".cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

CACHE_FILE = CACHE_DIR / "gemma27b_disaster_predictions.json"
MODEL_NAME = "gemma-3-27b-it"
CALL_DELAY = 4  # saniye — rate limit koruması

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.facecolor": "white",
})

# ── 18 Gösterge ─────────────────────────────────────────────────────────────
ALL_INDICATORS = [
    "people_trapped", "people_injured", "life_threat", "children_elderly_at_risk",
    "large_crowd_affected", "building_collapsed", "building_damaged", "road_blocked",
    "utility_disrupted", "utility_dangerous", "flood_water_rising", "fire_active",
    "landslide_active", "hazmat_present", "aftershock_risk", "no_communication",
    "area_isolated", "rescue_requested",
]

INDICATOR_CATEGORIES = {
    "people_trapped": "human", "people_injured": "human", "life_threat": "human",
    "children_elderly_at_risk": "human", "large_crowd_affected": "human",
    "building_collapsed": "infra", "building_damaged": "infra", "road_blocked": "infra",
    "utility_disrupted": "infra", "utility_dangerous": "infra",
    "flood_water_rising": "enviro", "fire_active": "enviro", "landslide_active": "enviro",
    "hazmat_present": "enviro", "aftershock_risk": "enviro",
    "no_communication": "comms", "area_isolated": "comms", "rescue_requested": "comms",
}

CATEGORY_COLORS = {
    "human": "#d73027", "infra": "#4575b4", "enviro": "#1a9850", "comms": "#ff7f00",
}

# ── Aciliyet skoru ağırlıkları (production ile birebir) ─────────────────────
INDICATOR_WEIGHTS = {
    "people_trapped": ("human", 2.5), "people_injured": ("human", 2.0),
    "life_threat": ("human", 2.0), "children_elderly_at_risk": ("human", 1.5),
    "large_crowd_affected": ("human", 1.0), "building_collapsed": ("infra", 2.0),
    "building_damaged": ("infra", 1.0), "road_blocked": ("infra", 1.5),
    "utility_disrupted": ("infra", 1.0), "utility_dangerous": ("infra", 1.5),
    "flood_water_rising": ("enviro", 1.5), "fire_active": ("enviro", 1.5),
    "landslide_active": ("enviro", 1.5), "hazmat_present": ("enviro", 1.0),
    "aftershock_risk": ("enviro", 1.0), "no_communication": ("comms", 0.8),
    "area_isolated": ("comms", 0.7), "rescue_requested": ("comms", 0.5),
}
CATEGORY_CAPS = {"human": 4.0, "infra": 3.0, "enviro": 2.0, "comms": 1.0}

# ── Ground Truth: Her raporun beklenen afet türü ───────────────────────────
GROUND_TRUTH = {
    "AKY-RPT-001": "heyelan",       "AKY-RPT-002": "heyelan",
    "AKY-RPT-003": "heyelan",       "AKY-RPT-004": "heyelan",
    "AKY-RPT-005": "heyelan",
    "AKY-RPT-006": "sel",           "AKY-RPT-007": "sel",
    "AKY-RPT-008": "sel",           "AKY-RPT-009": "sel",
    "AKY-RPT-010": "sel",
    "AKY-RPT-011": "sel",           "AKY-RPT-012": "sel",
    "AKY-RPT-013": "sel",           "AKY-RPT-014": "sel",
    "AKY-RPT-015": "sel",
    "AKY-RPT-016": "altyapi_hasari", "AKY-RPT-017": "altyapi_hasari",
    "AKY-RPT-018": "altyapi_hasari", "AKY-RPT-019": "altyapi_hasari",
    "AKY-RPT-020": "altyapi_hasari",
    "AKY-RPT-021": "sel",           "AKY-RPT-022": "sel",
    "AKY-RPT-023": "sel",           "AKY-RPT-024": "sel",
    "AKY-RPT-025": "sel",           "AKY-RPT-026": "sel",
    "AKY-RPT-027": "sel",           "AKY-RPT-028": "sel",
    "AKY-RPT-029": "sel",           "AKY-RPT-030": "sel",
    "AKY-RPT-031": "sel",           "AKY-RPT-032": "tibbi_acil",
    "AKY-RPT-033": "yapi_hasari",   "AKY-RPT-034": "yapi_hasari",
    "AKY-RPT-035": "sel",           "AKY-RPT-036": "tibbi_acil",
    "AKY-RPT-037": "altyapi_hasari", "AKY-RPT-038": "yapi_hasari",
    "AKY-RPT-039": "heyelan",       "AKY-RPT-040": "tibbi_acil",
    "AKY-RPT-041": "alakasiz",      "AKY-RPT-042": "alakasiz",
    "AKY-RPT-043": "alakasiz",      "AKY-RPT-044": "alakasiz",
    "AKY-RPT-045": "alakasiz",
    "AKY-RPT-046": "sel",           "AKY-RPT-047": "sel",
    "AKY-RPT-048": "sel",           "AKY-RPT-049": "tibbi_acil",
    "AKY-RPT-050": "sel",
}

ALL_DISASTER_TYPES = ["sel", "heyelan", "altyapi_hasari", "yapi_hasari", "tibbi_acil", "alakasiz"]

DISASTER_TYPE_LABELS_TR = {
    "sel": "Sel",
    "heyelan": "Heyelan",
    "altyapi_hasari": "Altyapı Hasarı",
    "yapi_hasari": "Yapı Hasarı",
    "tibbi_acil": "Tıbbi Acil",
    "alakasiz": "Alakasız",
}

DISASTER_TYPE_COLORS = {
    "sel": "#4393c3",
    "heyelan": "#8c6d31",
    "altyapi_hasari": "#e7298a",
    "yapi_hasari": "#d95f02",
    "tibbi_acil": "#e41a1c",
    "alakasiz": "#999999",
}

# ── Gemma Sistem Promptu ─────────────────────────────────────────────────────
_INDICATOR_SCHEMA = ", ".join([f'"{k}": boolean' for k in ALL_INDICATORS])

SYSTEM_PROMPT = f"""Sen bir Afet Karar Destek Sistemi yapay zekâsısın.
Afet bildirimi metnini analiz et, SADECE JSON üret.

KESİN KURALLAR:
- Asla serbest metin yazma, SADECE JSON döndür
- Emin olmadığın göstergeleri false yap
- Çelişkili verilerde İNSAN HAYATINI öncele → true yap
- Şemada olmayan alan ekleme

JSON ŞEMASI:
{{
  "disaster_type": "string (sel, heyelan, altyapi_hasari, yapi_hasari, tibbi_acil veya alakasiz)",
  "indicators": {{
    {_INDICATOR_SCHEMA}
  }}
}}"""

# ── Afet türü normalleştirme ─────────────────────────────────────────────────
# Gemma farklı yazım varyantları üretebilir; bunları ground truth anahtarlarına eşle.
_NORMALIZE_MAP = {
    "sel": "sel", "taşkın": "sel", "taşkin": "sel", "su baskını": "sel",
    "su_baskini": "sel", "flooding": "sel",
    "heyelan": "heyelan", "toprak kayması": "heyelan", "toprak_kayması": "heyelan",
    "landslide": "heyelan",
    "altyapi_hasari": "altyapi_hasari", "altyapı_hasarı": "altyapi_hasari",
    "altyapı hasarı": "altyapi_hasari", "altyapi hasari": "altyapi_hasari",
    "infrastructure": "altyapi_hasari", "altyapi": "altyapi_hasari",
    "elektrik": "altyapi_hasari", "utility": "altyapi_hasari",
    "yapi_hasari": "yapi_hasari", "yapı_hasarı": "yapi_hasari",
    "yapı hasarı": "yapi_hasari", "yapi hasari": "yapi_hasari",
    "bina hasarı": "yapi_hasari", "bina_hasari": "yapi_hasari",
    "structural": "yapi_hasari",
    "tibbi_acil": "tibbi_acil", "tıbbi_acil": "tibbi_acil",
    "tıbbi acil": "tibbi_acil", "tibbi acil": "tibbi_acil",
    "medical": "tibbi_acil", "yaralanma": "tibbi_acil",
    "alakasiz": "alakasiz", "alakasız": "alakasiz",
    "irrelevant": "alakasiz", "bilinmiyor": "alakasiz",
}


def normalize_disaster_type(raw: str) -> str:
    """Gemma çıktısını ground truth anahtarına normalleştir."""
    if not raw:
        return "alakasiz"
    cleaned = raw.strip().lower().replace("-", "_")
    # Direkt eşleşme
    if cleaned in _NORMALIZE_MAP:
        return _NORMALIZE_MAP[cleaned]
    # Kısmi eşleşme (örn. "sel/taşkın" → "sel")
    for key, val in _NORMALIZE_MAP.items():
        if key in cleaned:
            return val
    return "alakasiz"


# ── JSON ayrıştırma ──────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


# ── Gemma 3 27B API çağrısı ───────────────────────────────────────────────────
def call_gemma(report_id: str, text: str) -> dict | None:
    """Tek rapor için Gemma 3 27B çağrısı yapar; JSON döndürür."""
    from google import genai
    from google.genai import types

    user_message = f"{SYSTEM_PROMPT}\n\nAfet bildirimi:\n{text}"
    try:
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[user_message],
            config=types.GenerateContentConfig(temperature=0.1),
        )
        raw = response.text.strip()
        parsed = _extract_json(raw)
        if parsed is None:
            print(f"  ⚠️  {report_id}: JSON parse edilemedi → {raw[:120]}")
        return parsed
    except Exception as e:
        print(f"  ❌  {report_id}: API hatası → {e}")
        return None


# ── Veri yükleme ─────────────────────────────────────────────────────────────
def load_reports() -> list[dict]:
    with open(ROOT / "backend" / "seed" / "test_reports.json", encoding="utf-8") as f:
        data = json.load(f)
    return data["raw_reports"]


# ── Tahminleri al (cache destekli) ───────────────────────────────────────────
def get_predictions(reports: list[dict]) -> dict[str, dict]:
    """
    Her rapor için Gemma'dan disaster_type + indicators al.
    Daha önce alınanlar cache'ten yüklenir.
    """
    cache: dict[str, dict] = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  📦  Cache yüklendi: {len(cache)} rapor")

    missing = [r for r in reports if r["report_id"] not in cache]
    if missing:
        print(f"  🌐  {len(missing)} rapor için Gemma 3 27B çağrılıyor...")
        for i, r in enumerate(missing, 1):
            rid = r["report_id"]
            text = f"{r.get('event_define', '')} {r.get('event_capture', '')}".strip()
            print(f"     [{i}/{len(missing)}] {rid}...", end=" ", flush=True)
            result = call_gemma(rid, text)
            if result:
                cache[rid] = result
                print("✓")
            else:
                # API başarısız → boş tahmin (alakasiz, tüm göstergeler false)
                cache[rid] = {
                    "disaster_type": "alakasiz",
                    "indicators": {k: False for k in ALL_INDICATORS},
                }
                print("✗ (fallback)")
            time.sleep(CALL_DELAY)

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"  💾  Cache güncellendi: {CACHE_FILE.name}")
    else:
        print("  ✅  Tüm tahminler cache'ten yüklendi, API çağrısı yok.")

    return cache


# ── Aciliyet skoru ───────────────────────────────────────────────────────────
def compute_severity(indicators: dict) -> float:
    cats = {"human": 0.0, "infra": 0.0, "enviro": 0.0, "comms": 0.0}
    for k, (cat, w) in INDICATOR_WEIGHTS.items():
        if indicators.get(k, False):
            cats[cat] += w
    total = sum(min(v, CATEGORY_CAPS[c]) for c, v in cats.items())
    return round(max(total, 1.0), 1)


# ── Analiz ───────────────────────────────────────────────────────────────────
def analyze_all(reports: list[dict], predictions: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for r in reports:
        rid = r["report_id"]
        text = f"{r.get('event_define', '')} {r.get('event_capture', '')}".strip()
        pred = predictions.get(rid, {})

        raw_type = pred.get("disaster_type", "alakasiz")
        predicted_type = normalize_disaster_type(raw_type)
        expected_type = GROUND_TRUTH.get(rid, "bilinmiyor")

        indicators = {k: bool(pred.get("indicators", {}).get(k, False)) for k in ALL_INDICATORS}
        severity = compute_severity(indicators)
        active_count = sum(1 for v in indicators.values() if v)

        row = {
            "report_id": rid,
            "text": text[:100],
            "expected_type": expected_type,
            "predicted_type": predicted_type,
            "raw_gemma_type": raw_type,
            "type_correct": predicted_type == expected_type,
            "severity": severity,
            "active_indicators": active_count,
        }
        for ind in ALL_INDICATORS:
            row[ind] = indicators[ind]
        rows.append(row)
    return pd.DataFrame(rows)


# ── Plot 1: 50×18 Gösterge Isı Haritası ─────────────────────────────────────
def plot_indicator_heatmap(df: pd.DataFrame) -> None:
    print("  📊 Gösterge tespit ısı haritası...")
    heat = df.set_index("report_id")[ALL_INDICATORS].astype(int)

    fig, ax = plt.subplots(figsize=(16, 18))
    sns.heatmap(
        heat, ax=ax, cmap="YlGn", vmin=0, vmax=1,
        linewidths=0.3, linecolor="#eeeeee",
        cbar_kws={"label": "0 = Tespit edilmedi   |   1 = Tespit edildi", "shrink": 0.4},
        xticklabels=[ind.replace("_", "\n") for ind in ALL_INDICATORS],
    )

    # Sağ kenar: beklenen tür renk çubuğu
    for i, (_, row) in enumerate(df.iterrows()):
        etype = row["expected_type"]
        color = DISASTER_TYPE_COLORS.get(etype, "#cccccc")
        ax.add_patch(plt.Rectangle((len(ALL_INDICATORS), i), 0.8, 1,
                                   color=color, transform=ax.transData, clip_on=False))

    ax.set_title(
        "Gemma 3 27B Gösterge Tespit Haritası (50 Rapor × 18 Gösterge)\n"
        "Sağ kenar: beklenen afet türü rengi",
        fontsize=13, pad=14,
    )
    ax.set_xlabel("Göstergeler", fontsize=11)
    ax.set_ylabel("Raporlar", fontsize=11)
    plt.xticks(fontsize=8, rotation=45, ha="right")
    plt.yticks(fontsize=7)

    legend_patches = [
        mpatches.Patch(color=c, label=DISASTER_TYPE_LABELS_TR[t])
        for t, c in DISASTER_TYPE_COLORS.items()
    ]
    ax.legend(handles=legend_patches, title="Afet Türü", loc="lower right",
              fontsize=8, title_fontsize=9, framealpha=0.9)

    fig.tight_layout()
    out = OUTPUT_DIR / "gemma27b_indicator_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 2: Sınıflandırma Başarısı ──────────────────────────────────────────
def plot_disaster_classification(df: pd.DataFrame) -> None:
    print("  📊 Afet türü sınıflandırma başarısı...")

    types_in_data = [t for t in ALL_DISASTER_TYPES
                     if t in df["expected_type"].values or t in df["predicted_type"].values]
    type_labels = [DISASTER_TYPE_LABELS_TR[t] for t in types_in_data]

    cm = np.zeros((len(types_in_data), len(types_in_data)), dtype=int)
    for _, row in df.iterrows():
        ei = types_in_data.index(row["expected_type"]) if row["expected_type"] in types_in_data else -1
        pi = types_in_data.index(row["predicted_type"]) if row["predicted_type"] in types_in_data else -1
        if ei >= 0 and pi >= 0:
            cm[ei, pi] += 1

    per_cat_acc = {}
    for t in types_in_data:
        subset = df[df["expected_type"] == t]
        per_cat_acc[t] = subset["type_correct"].mean() if len(subset) > 0 else 0.0

    overall_acc = df["type_correct"].mean()

    fig, (ax_cm, ax_bar) = plt.subplots(1, 2, figsize=(16, 7),
                                        gridspec_kw={"width_ratios": [1.3, 1]})

    sns.heatmap(
        cm, ax=ax_cm, annot=True, fmt="d", cmap="Blues",
        xticklabels=type_labels, yticklabels=type_labels,
        linewidths=0.5, linecolor="lightgray",
    )
    ax_cm.set_xlabel("Tahmin Edilen (Gemma 3 27B)", fontsize=11)
    ax_cm.set_ylabel("Beklenen (Ground Truth)", fontsize=11)
    ax_cm.set_title(
        f"Afet Türü Confusion Matrix\nGenel Doğruluk: {overall_acc:.1%}",
        fontsize=12, pad=10,
    )
    plt.setp(ax_cm.get_xticklabels(), rotation=30, ha="right", fontsize=9)
    plt.setp(ax_cm.get_yticklabels(), rotation=0, fontsize=9)

    cats = list(per_cat_acc.keys())
    accs = [per_cat_acc[c] for c in cats]
    cat_labels = [DISASTER_TYPE_LABELS_TR[c] for c in cats]
    cat_colors = [DISASTER_TYPE_COLORS[c] for c in cats]
    counts = [len(df[df["expected_type"] == c]) for c in cats]

    bars = ax_bar.barh(cat_labels, accs, color=cat_colors, edgecolor="white", height=0.6)
    for bar, acc, cnt in zip(bars, accs, counts):
        ax_bar.text(bar.get_width() + 0.02, bar.get_y() + bar.get_height() / 2,
                    f"{acc:.0%} ({cnt} rapor)", va="center", fontsize=10)
    ax_bar.axvline(overall_acc, color="black", linestyle="--", linewidth=1.2,
                   label=f"Genel: {overall_acc:.0%}")
    ax_bar.set_xlim(0, 1.25)
    ax_bar.set_xlabel("Doğruluk Oranı", fontsize=11)
    ax_bar.set_title("Kategori Başına Sınıflandırma Doğruluğu", fontsize=12, pad=10)
    ax_bar.legend(fontsize=9)

    fig.suptitle("Gemma 3 27B — Afet Türü Sınıflandırma Başarısı", fontsize=14, y=1.02)
    fig.tight_layout()
    out = OUTPUT_DIR / "gemma27b_disaster_classification.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 3: Gösterge Frekansları ─────────────────────────────────────────────
def plot_indicator_frequency(df: pd.DataFrame) -> None:
    print("  📊 Gösterge frekans çubuğu...")
    n = len(df)
    freq = {ind: df[ind].sum() / n * 100 for ind in ALL_INDICATORS}
    sorted_inds = sorted(freq, key=freq.get, reverse=True)  # type: ignore[arg-type]
    values = [freq[i] for i in sorted_inds]
    colors = [CATEGORY_COLORS[INDICATOR_CATEGORIES[i]] for i in sorted_inds]

    fig, ax = plt.subplots(figsize=(13, 7))
    bars = ax.barh(sorted_inds, values, color=colors, edgecolor="white", height=0.7)
    for bar, val in zip(bars, values):
        label = f"{val:.1f}% ({int(val * n / 100)} rapor)"
        ax.text(val + 0.8, bar.get_y() + bar.get_height() / 2,
                label, va="center", fontsize=9)

    ax.set_xlabel("Raporlarda Tespit Oranı (%)", fontsize=11)
    ax.set_title("Gemma 3 27B Gösterge Tespit Frekansları (50 Rapor)", fontsize=13, pad=10)
    ax.set_xlim(0, max(values) * 1.25 if values else 10)

    legend_elements = [mpatches.Patch(color=v, label=k.capitalize())
                       for k, v in CATEGORY_COLORS.items()]
    ax.legend(handles=legend_elements, title="Kategori", loc="lower right", fontsize=9)

    fig.tight_layout()
    out = OUTPUT_DIR / "gemma27b_indicator_frequency.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 4: Aciliyet Skoru Analizi ───────────────────────────────────────────
def plot_severity_analysis(df: pd.DataFrame) -> None:
    print("  📊 Aciliyet skoru analizi...")

    fig, (ax_hist, ax_box) = plt.subplots(1, 2, figsize=(15, 6))

    bins = np.arange(0.5, 11.5, 1)
    ax_hist.hist(df["severity"], bins=bins, color="#4575b4", edgecolor="white",
                 linewidth=1.2, alpha=0.85, rwidth=0.85)
    ax_hist.axvline(df["severity"].mean(), color="red", linestyle="--", linewidth=1.5,
                    label=f"Ortalama: {df['severity'].mean():.1f}")
    ax_hist.axvline(df["severity"].median(), color="orange", linestyle="-.", linewidth=1.5,
                    label=f"Medyan: {df['severity'].median():.1f}")

    for threshold, color, label in [
        (3, "#999", "Düşük sınırı"),
        (5, "#e6a800", "Orta sınırı"),
        (7, "#d73027", "Kritik sınırı"),
    ]:
        ax_hist.axvline(threshold, color=color, linestyle=":", linewidth=1.0, alpha=0.6)
        ax_hist.text(threshold + 0.1, ax_hist.get_ylim()[1] * 0.92, label,
                     fontsize=7, color=color, rotation=90, va="top")

    ax_hist.set_xlabel("Aciliyet Skoru (1–10)", fontsize=11)
    ax_hist.set_ylabel("Rapor Sayısı", fontsize=11)
    ax_hist.set_title("Aciliyet Skoru Dağılımı", fontsize=12)
    ax_hist.legend(fontsize=9)
    ax_hist.set_xticks(range(1, 11))

    type_order = [t for t in ALL_DISASTER_TYPES if t in df["expected_type"].values]
    type_labels = [DISASTER_TYPE_LABELS_TR[t] for t in type_order]
    type_colors = [DISASTER_TYPE_COLORS[t] for t in type_order]

    box_data = [df[df["expected_type"] == t]["severity"].tolist() for t in type_order]
    bp = ax_box.boxplot(box_data, patch_artist=True, labels=type_labels,
                        medianprops=dict(color="black", linewidth=2),
                        widths=0.5)
    for patch, color in zip(bp["boxes"], type_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax_box.set_ylabel("Aciliyet Skoru (1–10)", fontsize=11)
    ax_box.set_title("Afet Türüne Göre Aciliyet Skoru", fontsize=12)
    plt.setp(ax_box.get_xticklabels(), rotation=25, ha="right", fontsize=9)
    ax_box.set_ylim(0, 11)

    fig.suptitle("Gemma 3 27B → Deterministik Puanlama — Aciliyet Analizi", fontsize=14, y=1.02)
    fig.tight_layout()
    out = OUTPUT_DIR / "gemma27b_severity_analysis.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── CSV: Tam Sonuç Tablosu ────────────────────────────────────────────────────
def save_summary_csv(df: pd.DataFrame) -> None:
    cols = [
        "report_id", "text", "expected_type", "predicted_type", "raw_gemma_type",
        "type_correct", "severity", "active_indicators",
    ] + ALL_INDICATORS
    out = OUTPUT_DIR / "gemma27b_summary.csv"
    df[cols].to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  💾  {out}")


# ── Ana Akış ──────────────────────────────────────────────────────────────────
def main():
    print("🚀 Gemma 3 27B Afet Türü Sınıflandırma Analizi")
    print(f"   Model: {MODEL_NAME}\n")

    reports = load_reports()
    print(f"📡 {len(reports)} rapor yüklendi\n")

    print("🔮 Tahminler alınıyor...")
    predictions = get_predictions(reports)

    print("\n📐 Analiz yapılıyor...")
    df = analyze_all(reports, predictions)
    overall_acc = df["type_correct"].mean()
    avg_severity = df["severity"].mean()
    avg_active = df["active_indicators"].mean()

    print(f"\n📊 Sonuçlar:")
    print(f"   Sınıflandırma doğruluğu : {overall_acc:.1%} ({df['type_correct'].sum()}/{len(df)})")
    print(f"   Ortalama aciliyet skoru : {avg_severity:.1f}")
    print(f"   Ort. aktif gösterge     : {avg_active:.1f}\n")

    wrong = df[~df["type_correct"]]
    if len(wrong) > 0:
        print(f"   ⚠️  Yanlış sınıflandırılan {len(wrong)} rapor:")
        for _, row in wrong.iterrows():
            expected_tr = DISASTER_TYPE_LABELS_TR.get(row["expected_type"], row["expected_type"])
            predicted_tr = DISASTER_TYPE_LABELS_TR.get(row["predicted_type"], row["predicted_type"])
            print(f"      {row['report_id']}: beklenen={expected_tr}, "
                  f"tahmin={predicted_tr} (Gemma raw: '{row['raw_gemma_type']}')")
        print()

    print("🎨 Grafikler oluşturuluyor...")
    plot_indicator_heatmap(df)
    plot_disaster_classification(df)
    plot_indicator_frequency(df)
    plot_severity_analysis(df)
    save_summary_csv(df)

    print(f"\n✅ Tüm çıktılar: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
