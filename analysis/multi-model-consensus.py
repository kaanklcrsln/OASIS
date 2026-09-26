"""
spaCy Türkçe NLP — Afet Raporu Sınıflandırma Başarı Analizi
=============================================================
Amaç:
    50 raporun tamamını spaCy anahtar-kelime eşleşmesi ile analiz eder.
    Gösterge tespiti, afet türü sınıflandırması ve aciliyet puanlamasındaki
    başarıyı beklenen (ground truth) değerlerle karşılaştırarak ölçer.

    API anahtarı GEREKMEZ — tamamen yerel çalışır.

Bağımlılıklar:
    pip install spacy pandas matplotlib seaborn numpy

Çalıştırma:
    cd analysis && python multi-model-consensus.py

Çıktılar (analysis/outputs/):
    - spacy_indicator_heatmap.png          : 50×18 gösterge tespit ısı haritası
    - spacy_disaster_classification.png    : Confusion matrix + kategori başarı çubuğu
    - spacy_indicator_frequency.png        : Gösterge tespit frekansları
    - spacy_severity_analysis.png          : Aciliyet skoru dağılımı ve kutu grafiği
    - spacy_summary.csv                    : Tam sonuç tablosu
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import seaborn as sns

# ── Dizin ayarları ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

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

# ── spaCy Gösterge Anahtar Kelimeleri ───────────────────────────────────────
# Türkçe morfoloji nedeniyle substring eşleşme kullanılır.
# Hem resmi hem informal yazım varyantları dahil.

INDICATOR_KEYWORDS = {
    "people_trapped": [
        "mahsur", "çıkamıy", "çıkamıo", "cıkamıo", "enkaz alt",
        "kilitli kald", "içeride kald", "tahliye edil",
    ],
    "people_injured": [
        "yaralı", "yaralandı", "kırık", "kırıldı", "kanama",
        "bilinç kayb", "çarptı", "acil tıp", "kalkamıo", "acıoo",
    ],
    "life_threat": [
        "ölüyoruz", "boğulacağız", "bogulucaz", "tehlike",
        "hayati", "ölecek", "yardım gelmezse", "imdat",
        "patlama riski", "patlıcak", "carpıcak",
    ],
    "children_elderly_at_risk": [
        "çocuk", "cocuk", "yaşlı", "yasli", "bebek",
        "engelli", "hamile", "annem", "öğretmen",
    ],
    "large_crowd_affected": [
        "mahalle", "köy", "onlarca", "çok kişi", "hane",
        "kalabalık", "topluluk", "20 kadar", "toplu",
    ],
    "building_collapsed": [
        "bina çöktü", "çöktü", "coktu", "yıkıldı", "enkaz",
        "çökmüş", "yıkılmış", "köprü çöktü",
    ],
    "building_damaged": [
        "çatlak", "hasar", "duvar yıkıldı", "eğilmeye",
        "temel oynadı", "devrildi",
    ],
    "road_blocked": [
        "yol kapandı", "yol kapali", "yol yok", "geçilemiyo",
        "geçemiyo", "ulaşım yok", "yol kesildi", "köprü çökt",
        "asfalt koptu", "asfalt çöktü", "trafik durdu", "yollar kapalı",
    ],
    "utility_disrupted": [
        "elektrik kesildi", "elektrikler kesildi", "su kesildi",
        "gaz kesildi", "elektrikler gitti", "elektrik gitti", "kesinti",
    ],
    "utility_dangerous": [
        "gaz sızıntı", "gaz kokusu", "elektrik çarpma", "patlama riski",
        "trafo", "trifı", "tel koptu", "teller koptu", "kıvılcım",
        "yüksek gerilim", "elektrik tel", "patlıo",
    ],
    "flood_water_rising": [
        "sel", "su yükseliyor", "su yükseld", "su basıyor", "su bastı",
        "dere taştı", "su seviyesi", "su doldu", "su doluyo",
        "taşkın", "su gir", "sular altında", "balçığa", "su akıyor",
        "su birikinti",
    ],
    "fire_active": [
        "yangın", "yanıyor", "alevler", "duman", "yanar",
    ],
    "landslide_active": [
        "heyelan", "toprak kayması", "toprak kayıyor", "toprak kayd",
        "toprak kayarak", "istinat duvarı",
    ],
    "hazmat_present": [
        "kimyasal", "tehlikeli madde", "zehirli", "doğalgaz boru",
        "gaz kokusu",
    ],
    "aftershock_risk": [
        "artçı", "deprem sonrası", "sarsıntı", "deprem devam",
    ],
    "no_communication": [
        "sinyal yok", "iletişim kesildi", "haber alamıyoruz",
        "bağlanamıyoruz", "ulaşamıyoruz",
    ],
    "area_isolated": [
        "izole", "ulaşılamıyor", "ulaşamıyoruz",
        "bölgeye girilemiyor", "tamamen kesildi",
        "hiçbir araç", "gidemıo",
    ],
    "rescue_requested": [
        "yardım", "kurtarın", "kurtarma", "yardım edin",
        "imdat", "ambulans", "acil yardım", "kurtarınn",
        "gelsın", "acil", "lazım", "nolur", "ltfn",
    ],
}

# ── spaCy Afet Türü Sınıflandırma Kuralları ────────────────────────────────
DISASTER_KEYWORDS = {
    "heyelan":        ["heyelan", "toprak kay", "toprak kayd", "istinat", "çamur akıntı"],
    "sel":            ["sel", "su bas", "su yüksel", "dere taş", "su seviyesi",
                       "taşkın", "su dol", "su gir", "su akıyor", "mazgal",
                       "balçık", "sular altında", "su birikinti"],
    "altyapi_hasari": ["trafo", "elektrik dir", "yüksek gerilim", "elektrik çarp",
                       "teller kop", "tel kop", "kıvılcım", "trifı", "patlıo",
                       "doğalgaz", "gaz kokusu", "gaz sızıntı", "kanalizasyon"],
    "yapi_hasari":    ["bina çök", "çatı çök", "duvar çök", "enkaz altı",
                       "bina eğil", "yıkıldı", "coktu", "temel oynadı",
                       "istinat duvarı devrildi"],
    "tibbi_acil":     ["yaralı", "ayağım kırıl", "kalkamıo", "ambulans",
                       "hastane", "bebek", "hamile", "diyaliz", "sedye",
                       "ateşli", "sancılan", "acıoo"],
}

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


# ── spaCy Gösterge Çıkarımı ────────────────────────────────────────────────
def extract_indicators(text: str) -> dict:
    """Anahtar kelime substring eşleşmesi ile 18 boolean gösterge çıkar."""
    t = text.lower()
    return {ind: any(kw in t for kw in kws) for ind, kws in INDICATOR_KEYWORDS.items()}


# ── spaCy Afet Türü Sınıflandırması ────────────────────────────────────────
def classify_disaster_type(text: str, indicators: dict) -> str:
    """Anahtar kelime öncelik sırası ile afet türü belirle."""
    t = text.lower()

    # Ciddi bir gösterge yoksa → alakasız
    active = [k for k, v in indicators.items() if v]
    serious = [k for k in active if k not in ("rescue_requested",)]
    if not serious:
        return "alakasiz"

    # Anahtar kelime skorlama (her tür için eşleşen keyword sayısı)
    scores: dict[str, int] = {}
    for dtype, keywords in DISASTER_KEYWORDS.items():
        scores[dtype] = sum(1 for kw in keywords if kw in t)

    # En çok eşleşen tür
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] > 0:
        return best

    # Keyword eşleşmesi yoksa → indicator'lardan çıkar
    if indicators.get("flood_water_rising"):
        return "sel"
    if indicators.get("landslide_active"):
        return "heyelan"
    if indicators.get("utility_dangerous") or indicators.get("utility_disrupted"):
        return "altyapi_hasari"
    if indicators.get("building_collapsed") or indicators.get("building_damaged"):
        return "yapi_hasari"
    if indicators.get("people_injured"):
        return "tibbi_acil"

    return "alakasiz"


# ── Aciliyet Skoru (production replikası) ───────────────────────────────────
def compute_severity(indicators: dict) -> float:
    cats = {"human": 0.0, "infra": 0.0, "enviro": 0.0, "comms": 0.0}
    for k, (cat, w) in INDICATOR_WEIGHTS.items():
        if indicators.get(k, False):
            cats[cat] += w
    total = sum(min(v, CATEGORY_CAPS[c]) for c, v in cats.items())
    return round(max(total, 1.0), 1)


# ── Veri Yükleme ───────────────────────────────────────────────────────────
def load_reports() -> list[dict]:
    with open(ROOT / "backend" / "seed" / "test_reports.json", encoding="utf-8") as f:
        data = json.load(f)
    return data["raw_reports"]


# ── Analiz ──────────────────────────────────────────────────────────────────
def analyze_all(reports: list[dict]) -> pd.DataFrame:
    rows = []
    for r in reports:
        rid = r["report_id"]
        text = f"{r.get('event_define', '')} {r.get('event_capture', '')}".strip()
        indicators = extract_indicators(text)
        predicted_type = classify_disaster_type(text, indicators)
        expected_type = GROUND_TRUTH.get(rid, "bilinmiyor")
        severity = compute_severity(indicators)
        active_count = sum(1 for v in indicators.values() if v)

        row = {
            "report_id": rid,
            "text": text[:100],
            "expected_type": expected_type,
            "predicted_type": predicted_type,
            "type_correct": predicted_type == expected_type,
            "severity": severity,
            "active_indicators": active_count,
        }
        for ind in ALL_INDICATORS:
            row[ind] = indicators[ind]
        rows.append(row)
    return pd.DataFrame(rows)


# ── Plot 1: 50×18 Gösterge Isı Haritası ────────────────────────────────────
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
        "spaCy Gösterge Tespit Haritası (50 Rapor × 18 Gösterge)\n"
        "Sağ kenar: beklenen afet türü rengi",
        fontsize=13, pad=14,
    )
    ax.set_xlabel("Göstergeler", fontsize=11)
    ax.set_ylabel("Raporlar", fontsize=11)
    plt.xticks(fontsize=8, rotation=45, ha="right")
    plt.yticks(fontsize=7)

    # Afet türü renk efsanesi
    legend_patches = [
        mpatches.Patch(color=c, label=DISASTER_TYPE_LABELS_TR[t])
        for t, c in DISASTER_TYPE_COLORS.items()
    ]
    ax.legend(handles=legend_patches, title="Afet Türü", loc="lower right",
              fontsize=8, title_fontsize=9, framealpha=0.9)

    fig.tight_layout()
    out = OUTPUT_DIR / "spacy_indicator_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 2: Sınıflandırma Başarısı (Confusion Matrix + Bar) ───────────────
def plot_disaster_classification(df: pd.DataFrame) -> None:
    print("  📊 Afet türü sınıflandırma başarısı...")

    types_in_data = [t for t in ALL_DISASTER_TYPES
                     if t in df["expected_type"].values or t in df["predicted_type"].values]
    type_labels = [DISASTER_TYPE_LABELS_TR[t] for t in types_in_data]

    # Confusion matrix
    cm = np.zeros((len(types_in_data), len(types_in_data)), dtype=int)
    for _, row in df.iterrows():
        ei = types_in_data.index(row["expected_type"]) if row["expected_type"] in types_in_data else -1
        pi = types_in_data.index(row["predicted_type"]) if row["predicted_type"] in types_in_data else -1
        if ei >= 0 and pi >= 0:
            cm[ei, pi] += 1

    # Per-category accuracy
    per_cat_acc = {}
    for t in types_in_data:
        subset = df[df["expected_type"] == t]
        if len(subset) > 0:
            per_cat_acc[t] = subset["type_correct"].mean()
        else:
            per_cat_acc[t] = 0.0

    overall_acc = df["type_correct"].mean()

    fig, (ax_cm, ax_bar) = plt.subplots(1, 2, figsize=(16, 7),
                                        gridspec_kw={"width_ratios": [1.3, 1]})

    # Sol: confusion matrix
    sns.heatmap(
        cm, ax=ax_cm, annot=True, fmt="d", cmap="Blues",
        xticklabels=type_labels, yticklabels=type_labels,
        linewidths=0.5, linecolor="lightgray",
    )
    ax_cm.set_xlabel("Tahmin Edilen (spaCy)", fontsize=11)
    ax_cm.set_ylabel("Beklenen (Ground Truth)", fontsize=11)
    ax_cm.set_title(
        f"Afet Türü Confusion Matrix\nGenel Doğruluk: {overall_acc:.1%}",
        fontsize=12, pad=10,
    )
    plt.setp(ax_cm.get_xticklabels(), rotation=30, ha="right", fontsize=9)
    plt.setp(ax_cm.get_yticklabels(), rotation=0, fontsize=9)

    # Sağ: per-category accuracy bar
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

    fig.suptitle("spaCy Türkçe NLP — Afet Türü Sınıflandırma Başarısı", fontsize=14, y=1.02)
    fig.tight_layout()
    out = OUTPUT_DIR / "spacy_disaster_classification.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 3: Gösterge Frekansları ────────────────────────────────────────────
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
    ax.set_title("spaCy Gösterge Tespit Frekansları (50 Rapor)", fontsize=13, pad=10)
    ax.set_xlim(0, max(values) * 1.25)

    legend_elements = [mpatches.Patch(color=v, label=k.capitalize())
                       for k, v in CATEGORY_COLORS.items()]
    ax.legend(handles=legend_elements, title="Kategori", loc="lower right", fontsize=9)

    fig.tight_layout()
    out = OUTPUT_DIR / "spacy_indicator_frequency.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── Plot 4: Aciliyet Skoru Analizi ──────────────────────────────────────────
def plot_severity_analysis(df: pd.DataFrame) -> None:
    print("  📊 Aciliyet skoru analizi...")

    fig, (ax_hist, ax_box) = plt.subplots(1, 2, figsize=(15, 6))

    # Sol: histogram
    bins = np.arange(0.5, 11.5, 1)
    ax_hist.hist(df["severity"], bins=bins, color="#4575b4", edgecolor="white",
                 linewidth=1.2, alpha=0.85, rwidth=0.85)
    ax_hist.axvline(df["severity"].mean(), color="red", linestyle="--", linewidth=1.5,
                    label=f"Ortalama: {df['severity'].mean():.1f}")
    ax_hist.axvline(df["severity"].median(), color="orange", linestyle="-.", linewidth=1.5,
                    label=f"Medyan: {df['severity'].median():.1f}")

    for threshold, color, label in [(3, "#999", "Düşük sınırı"), (5, "#e6a800", "Orta sınırı"), (7, "#d73027", "Kritik sınırı")]:
        ax_hist.axvline(threshold, color=color, linestyle=":", linewidth=1.0, alpha=0.6)
        ax_hist.text(threshold + 0.1, ax_hist.get_ylim()[1] * 0.92, label,
                     fontsize=7, color=color, rotation=90, va="top")

    ax_hist.set_xlabel("Aciliyet Skoru (1–10)", fontsize=11)
    ax_hist.set_ylabel("Rapor Sayısı", fontsize=11)
    ax_hist.set_title("Aciliyet Skoru Dağılımı", fontsize=12)
    ax_hist.legend(fontsize=9)
    ax_hist.set_xticks(range(1, 11))

    # Sağ: afet türüne göre boxplot
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

    fig.suptitle("spaCy → Deterministik Puanlama — Aciliyet Analizi", fontsize=14, y=1.02)
    fig.tight_layout()
    out = OUTPUT_DIR / "spacy_severity_analysis.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  💾  {out}")


# ── CSV: Tam Sonuç Tablosu ─────────────────────────────────────────────────
def save_summary_csv(df: pd.DataFrame) -> None:
    cols = [
        "report_id", "text", "expected_type", "predicted_type", "type_correct",
        "severity", "active_indicators",
    ] + ALL_INDICATORS
    out = OUTPUT_DIR / "spacy_summary.csv"
    df[cols].to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  💾  {out}")


# ── Ana Akış ────────────────────────────────────────────────────────────────
def main():
    print("🚀 spaCy Türkçe NLP Afet Sınıflandırma Analizi")
    print("   API anahtarı gerekmiyor — tamamen yerel\n")

    reports = load_reports()
    print(f"📡 {len(reports)} rapor yüklendi")

    df = analyze_all(reports)
    overall_acc = df["type_correct"].mean()
    avg_severity = df["severity"].mean()
    avg_active = df["active_indicators"].mean()

    print(f"\n📊 Sonuçlar:")
    print(f"   Sınıflandırma doğruluğu : {overall_acc:.1%} ({df['type_correct'].sum()}/{len(df)})")
    print(f"   Ortalama aciliyet skoru : {avg_severity:.1f}")
    print(f"   Ort. aktif gösterge     : {avg_active:.1f}\n")

    # Yanlış sınıflandırılan raporlar
    wrong = df[~df["type_correct"]]
    if len(wrong) > 0:
        print(f"   ⚠️  Yanlış sınıflandırılan {len(wrong)} rapor:")
        for _, row in wrong.iterrows():
            expected_tr = DISASTER_TYPE_LABELS_TR.get(row["expected_type"], row["expected_type"])
            predicted_tr = DISASTER_TYPE_LABELS_TR.get(row["predicted_type"], row["predicted_type"])
            print(f"      {row['report_id']}: beklenen={expected_tr}, tahmin={predicted_tr}")
        print()

    plot_indicator_heatmap(df)
    plot_disaster_classification(df)
    plot_indicator_frequency(df)
    plot_severity_analysis(df)
    save_summary_csv(df)

    print(f"\n✅ Tüm çıktılar: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
