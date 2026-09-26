"""
Dahili Tutarlılık ve Zamansal Kararlılık Analizi
=================================================
Amaç:
    Gemini 2.5 Flash modelini 50 raporun tamamında çalıştırır; gösterge çıkarımının
    mantıksal tutarlılığını ve tekrar çalıştırmalardaki kararlılığını ölçer.

Bağımlılıklar:
    pip install google-genai python-dotenv pandas matplotlib seaborn numpy

Çalıştırma:
    cd analysis && python internal-consistency.py

Önbellek:
    analysis/.cache/ic_predictions.json     — 50 rapor tahminleri
    analysis/.cache/ic_stability_runs.json  — 5 rapor × 3 tekrar

Çıktılar (analysis/outputs/):
    - ic_indicator_frequency.png   : Gösterge frekans çubuğu
    - ic_cooccurrence_heatmap.png  : 18×18 birlikte görünme ısı haritası
    - ic_consistency_rules.png     : Mantıksal tutarlılık kuralları
    - ic_stability_heatmap.png     : Kararlılık Jaccard ısı haritası
    - ic_summary.csv               : Gösterge özet tablosu
"""

import json
import os
import re
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ── Dizin ayarları ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
OUTPUT_DIR = Path(__file__).parent / "outputs"
CACHE_DIR = Path(__file__).parent / ".cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ── Ortam değişkenleri ──────────────────────────────────────────────────────
load_dotenv(ROOT / ".env")
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_BACKUP")

# ── Matplotlib ayarları ─────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.facecolor": "white",
})

# ── Model ve gecikme ────────────────────────────────────────────────────────
GEMMA_MODEL = "gemma-3-4b-it"
CALL_DELAY = 4  # saniye

# ── Göstergeler ─────────────────────────────────────────────────────────────
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

CATEGORY_COLORS = {"human": "crimson", "infra": "steelblue", "enviro": "forestgreen", "comms": "darkorange"}

# ── Sistem istemi ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Sen bir Afet Karar Destek Sistemi yapay zekâsısın.
Afet bildirimi metnini analiz et, SADECE JSON üret.

KESİN KURALLAR:
- Asla serbest metin yazma, SADECE JSON döndür
- Emin olmadığın göstergeleri false yap
- Çelişkili verilerde İNSAN HAYATINI öncele → true yap

JSON ŞEMASI:
{
  "indicators": {
    "people_trapped": boolean, "people_injured": boolean, "life_threat": boolean,
    "children_elderly_at_risk": boolean, "large_crowd_affected": boolean,
    "building_collapsed": boolean, "building_damaged": boolean, "road_blocked": boolean,
    "utility_disrupted": boolean, "utility_dangerous": boolean,
    "flood_water_rising": boolean, "fire_active": boolean, "landslide_active": boolean,
    "hazmat_present": boolean, "aftershock_risk": boolean, "no_communication": boolean,
    "area_isolated": boolean, "rescue_requested": boolean
  }
}"""

# ── Mantıksal tutarlılık kuralları ──────────────────────────────────────────
CONSISTENCY_RULES = [
    ("building_collapsed", "building_damaged",   "Bina çöktüyse hasar da olmalı"),
    ("fire_active",         "life_threat",        "Yangın varsa hayati tehlike"),
    ("utility_dangerous",   "life_threat",        "Tehlikeli altyapı = hayati risk"),
    ("hazmat_present",      "life_threat",        "Kimyasal madde = hayati risk"),
    ("people_trapped",      "rescue_requested",   "Mahsursa kurtarma istenmiş olmalı"),
    ("flood_water_rising",  "area_isolated",      "Sel = bölge izolasyonu"),
    ("building_collapsed",  "people_trapped",     "Enkaz altında insan olabilir"),
    ("landslide_active",    "road_blocked",       "Heyelan = yol kapanır"),
]

# ── Kararlılık testi raporları ───────────────────────────────────────────────
STABILITY_REPORT_IDS = [
    "AKY-RPT-001", "AKY-RPT-011", "AKY-RPT-020", "AKY-RPT-030", "AKY-RPT-045",
]


# ── JSON çıkarma ────────────────────────────────────────────────────────────

def extract_json(raw: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                return None
    return None


# ── API çağrısı ─────────────────────────────────────────────────────────────

def call_gemma(client: genai.Client, text: str) -> dict | None:
    """Gemma 3 4B ile gösterge çıkarımı — sistem promptu user mesajına merge edilir."""
    merged = f"{SYSTEM_PROMPT}\n\nAşağıdaki afet bildirimi metnini analiz et:\n\n{text}"
    try:
        response = client.models.generate_content(
            model=GEMMA_MODEL,
            contents=merged,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        parsed = extract_json(response.text)
        if parsed and "indicators" in parsed:
            return parsed["indicators"]
        return None
    except Exception as e:
        print(f"⚠️  API hatası: {e}")
        return None


# ── Veri yükleme ────────────────────────────────────────────────────────────

def load_reports() -> list:
    print("🔍 test_reports.json yükleniyor...")
    with open(ROOT / "backend" / "seed" / "test_reports.json", encoding="utf-8") as f:
        data = json.load(f)
    reports = data["raw_reports"]
    print(f"✅ {len(reports)} rapor yüklendi.")
    return reports


# ── Ana tahminler ────────────────────────────────────────────────────────────

def get_predictions(reports: list, client: genai.Client) -> dict:
    cache_path = CACHE_DIR / "ic_predictions.json"
    if cache_path.exists():
        print("💾 Önbellekten yükleniyor: ic_predictions.json")
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    print(f"🚀 {len(reports)} rapor için Gemini API çağrısı başlatılıyor...")
    predictions = {}
    for i, r in enumerate(reports):
        rid = r["report_id"]
        text = f"{r.get('event_define', '')} {r.get('event_capture', '')}".strip()
        print(f"   [{i+1:2d}/{len(reports)}] {rid}...", end=" ", flush=True)
        indicators = call_gemma(client, text)
        if indicators:
            predictions[rid] = indicators
            print("✅")
        else:
            print("⚠️  (None)")
        if i < len(reports) - 1:
            time.sleep(CALL_DELAY)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
    print(f"💾 Önbelleğe kaydedildi: {cache_path}")
    return predictions


# ── Kararlılık çalıştırmaları ────────────────────────────────────────────────

def get_stability_runs(reports: list, client: genai.Client) -> dict:
    cache_path = CACHE_DIR / "ic_stability_runs.json"
    if cache_path.exists():
        print("💾 Önbellekten yükleniyor: ic_stability_runs.json")
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    report_map = {r["report_id"]: r for r in reports}
    print(f"🚀 Kararlılık testi: {len(STABILITY_REPORT_IDS)} rapor × 3 tekrar...")
    stability = {rid: [] for rid in STABILITY_REPORT_IDS}

    for run_idx in range(3):
        print(f"   Tekrar {run_idx + 1}/3...")
        for rid in STABILITY_REPORT_IDS:
            r = report_map[rid]
            text = f"{r.get('event_define', '')} {r.get('event_capture', '')}".strip()
            indicators = call_gemma(client, text)
            if indicators:
                stability[rid].append(indicators)
            time.sleep(CALL_DELAY)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(stability, f, ensure_ascii=False, indent=2)
    print(f"💾 Önbelleğe kaydedildi: {cache_path}")
    return stability


# ── Görselleştirme 1: Gösterge frekansı ────────────────────────────────────

def plot_indicator_frequency(predictions: dict) -> None:
    print("🔍 Gösterge frekans grafiği oluşturuluyor...")
    n = len(predictions)
    freq = {}
    for ind in ALL_INDICATORS:
        count = sum(1 for preds in predictions.values() if preds.get(ind, False))
        freq[ind] = count / n * 100

    sorted_inds = sorted(freq, key=freq.get, reverse=True)
    values = [freq[ind] for ind in sorted_inds]
    colors = [CATEGORY_COLORS[INDICATOR_CATEGORIES[ind]] for ind in sorted_inds]

    fig, ax = plt.subplots(figsize=(12, 7))
    bars = ax.barh(sorted_inds, values, color=colors, edgecolor="white", linewidth=0.8)
    for bar, val in zip(bars, values):
        ax.text(val + 0.5, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%", va="center", fontsize=9)

    ax.set_xlabel("Raporlarda Görünme Oranı (%)")
    ax.set_title("Gösterge Frekans Analizi ( gemma-3-4b, 50 Rapor)", fontsize=13)
    ax.set_xlim(0, max(values) * 1.15)

    # Kategori efsanesi
    from matplotlib.patches import Patch
    legend_elements = [Patch(color=v, label=k) for k, v in CATEGORY_COLORS.items()]
    ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

    fig.tight_layout()
    out = OUTPUT_DIR / "ic_indicator_frequency.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"💾 {out}")


# ── Görselleştirme 2: Birlikte görünme ısı haritası ─────────────────────────

def plot_cooccurrence_heatmap(predictions: dict) -> None:
    print("🔍 Birlikte görünme ısı haritası oluşturuluyor...")
    n_ind = len(ALL_INDICATORS)
    matrix = np.zeros((n_ind, n_ind))

    for i, ind_a in enumerate(ALL_INDICATORS):
        a_true = [rid for rid, preds in predictions.items() if preds.get(ind_a, False)]
        if not a_true:
            continue
        for j, ind_b in enumerate(ALL_INDICATORS):
            b_given_a = sum(1 for rid in a_true if predictions[rid].get(ind_b, False))
            matrix[i, j] = b_given_a / len(a_true)

    short_names = [ind.replace("_", "\n") for ind in ALL_INDICATORS]
    df_heat = pd.DataFrame(matrix, index=short_names, columns=short_names)

    fig, ax = plt.subplots(figsize=(14, 12))
    sns.heatmap(
        df_heat, ax=ax, cmap="Blues", vmin=0, vmax=1,
        annot=True, fmt=".2f", annot_kws={"size": 7},
        linewidths=0.3, linecolor="lightgray",
    )
    ax.set_title("Gösterge Birlikte Görünme Olasılığı P(B=True | A=True)", fontsize=13, pad=15)
    ax.set_xlabel("B Göstergesi")
    ax.set_ylabel("A Göstergesi")
    plt.xticks(rotation=45, ha="right", fontsize=8)
    plt.yticks(rotation=0, fontsize=8)
    fig.tight_layout()
    out = OUTPUT_DIR / "ic_cooccurrence_heatmap.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"💾 {out}")


# ── Görselleştirme 3: Tutarlılık kuralları ──────────────────────────────────

def plot_consistency_rules(predictions: dict) -> None:
    print("🔍 Tutarlılık kuralları grafiği oluşturuluyor...")
    rates = []
    counts = []
    descriptions = []

    for ind_a, ind_b, desc in CONSISTENCY_RULES:
        a_true_rids = [rid for rid, p in predictions.items() if p.get(ind_a, False)]
        n_a = len(a_true_rids)
        if n_a == 0:
            rate = 0.0
        else:
            n_ab = sum(1 for rid in a_true_rids if predictions[rid].get(ind_b, False))
            rate = n_ab / n_a
        rates.append(rate)
        counts.append(n_a)
        descriptions.append(desc)

    colors = []
    for r in rates:
        if r < 0.5:
            colors.append("crimson")
        elif r < 0.8:
            colors.append("goldenrod")
        else:
            colors.append("forestgreen")

    fig, ax = plt.subplots(figsize=(12, 6))
    y_pos = range(len(descriptions))
    bars = ax.barh(list(y_pos), rates, color=colors, edgecolor="white", linewidth=0.8)

    for i, (bar, cnt) in enumerate(zip(bars, counts)):
        ax.text(
            bar.get_width() + 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{rates[i]:.0%}  (n={cnt})",
            va="center", fontsize=9,
        )

    ax.axvline(0.5, color="crimson", linestyle="--", linewidth=1.2, label="Eşik: 0.5")
    ax.axvline(0.8, color="forestgreen", linestyle="--", linewidth=1.2, label="Eşik: 0.8")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(descriptions, fontsize=9)
    ax.set_xlabel("Önerme Oranı (A=True → B=True)")
    ax.set_title("Mantıksal Tutarlılık Kuralları", fontsize=13)
    ax.set_xlim(0, 1.2)
    ax.legend(fontsize=9)
    fig.tight_layout()
    out = OUTPUT_DIR / "ic_consistency_rules.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"💾 {out}")


# ── Görselleştirme 4: Kararlılık ısı haritası ───────────────────────────────

def jaccard(ind_a: dict, ind_b: dict) -> float:
    set_a = {k for k, v in ind_a.items() if v}
    set_b = {k for k, v in ind_b.items() if v}
    if not set_a and not set_b:
        return 1.0
    union = len(set_a | set_b)
    if union == 0:
        return 1.0
    return len(set_a & set_b) / union


def plot_stability_heatmap(stability: dict) -> None:
    print("🔍 Kararlılık ısı haritası oluşturuluyor...")
    report_ids = list(stability.keys())
    n_reports = len(report_ids)

    # 5 rapor × (3 tekrar çifti = 3 kombinasyon): matris 5 × 3
    pair_labels = ["Tekrar 1-2", "Tekrar 1-3", "Tekrar 2-3"]
    matrix = np.zeros((n_reports, 3))

    all_scores = []
    for i, rid in enumerate(report_ids):
        runs = stability[rid]
        if len(runs) < 3:
            continue
        pairs = [(0, 1), (0, 2), (1, 2)]
        for j, (r1, r2) in enumerate(pairs):
            s = jaccard(runs[r1], runs[r2])
            matrix[i, j] = s
            all_scores.append(s)

    mean_stability = np.mean(all_scores) if all_scores else 0.0

    df_heat = pd.DataFrame(matrix, index=report_ids, columns=pair_labels)

    fig, ax = plt.subplots(figsize=(8, 5))
    sns.heatmap(
        df_heat, ax=ax, cmap="RdYlGn", vmin=0, vmax=1,
        annot=True, fmt=".2f", annot_kws={"size": 11},
        linewidths=0.5, linecolor="lightgray",
    )
    ax.set_title(
        f"Gösterge Kararlılık Analizi (Jaccard Benzerliği)\nOrtalama Kararlılık: {mean_stability:.3f}",
        fontsize=12,
    )
    ax.set_xlabel("Tekrar Çifti")
    ax.set_ylabel("Rapor ID")
    fig.tight_layout()
    out = OUTPUT_DIR / "ic_stability_heatmap.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"💾 {out}")


# ── CSV: Özet tablosu ────────────────────────────────────────────────────────

def save_summary_csv(predictions: dict) -> None:
    print("🔍 Özet CSV oluşturuluyor...")
    n = len(predictions)
    rows = []
    for ind in ALL_INDICATORS:
        n_true = sum(1 for p in predictions.values() if p.get(ind, False))
        rows.append({
            "indicator": ind,
            "frequency_pct": round(n_true / n * 100, 2),
            "n_true": n_true,
            "category": INDICATOR_CATEGORIES[ind],
        })
    df = pd.DataFrame(rows)
    out = OUTPUT_DIR / "ic_summary.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"💾 {out}")


# ── Ana akış ────────────────────────────────────────────────────────────────

def main() -> None:
    print("🚀 Dahili Tutarlılık Analizi başlatıldı")

    if not API_KEY:
        print("⚠️  API anahtarı bulunamadı. .env dosyasını kontrol edin.")
        return

    client = genai.Client(api_key=API_KEY)
    reports = load_reports()

    predictions = get_predictions(reports, client)
    print(f"✅ {len(predictions)} rapor tahmini mevcut.")

    stability = get_stability_runs(reports, client)
    print(f"✅ Kararlılık verileri mevcut ({len(stability)} rapor).")

    plot_indicator_frequency(predictions)
    plot_cooccurrence_heatmap(predictions)
    plot_consistency_rules(predictions)
    plot_stability_heatmap(stability)
    save_summary_csv(predictions)

    print("\n✅ Tüm çıktılar oluşturuldu → analysis/outputs/")


if __name__ == "__main__":
    main()
