"""
Pertürbasyon Sağlamlık Analizi
================================
Amaç:
    Gemini 2.5 Flash'ın aynı metni farklı biçimlerde gördüğünde tutarlı gösterge
    çıkarımı yapıp yapmadığını ölçer. 10 rapor × 4 pertürbasyon türü = 40 ekstra API
    çağrısı (önbellek varsa sıfır).

Bağımlılıklar:
    pip install google-genai python-dotenv pandas matplotlib seaborn numpy

Çalıştırma:
    cd analysis && python perturbation-robustness.py

Önbellek:
    analysis/.cache/pr_predictions.json  — {report_id: {pertürbasyon: indicators}}

Çıktılar (analysis/outputs/):
    - pr_stability_boxplot.png   : Pertürbasyon türüne göre Jaccard benzerliği kutu grafiği
    - pr_report_heatmap.png      : 10 rapor × 4 pertürbasyon Jaccard ısı haritası
    - pr_flip_frequency.png      : Gösterge başına toplam değişim sayısı
    - pr_summary.csv             : Rapor başına Jaccard özet tablosu
"""

import json
import os
import re
import sys
import time
import random
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import seaborn as sns
from dotenv import load_dotenv

# ── Ortam ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_BACKUP")
if not API_KEY:
    sys.exit("❌  GEMINI_API_KEY bulunamadı. .env dosyasını kontrol edin.")

OUTPUT_DIR = Path(__file__).parent / "outputs"
CACHE_DIR  = Path(__file__).parent / ".cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

CACHE_FILE  = CACHE_DIR / "pr_predictions_gemma4b.json"
MODEL_NAME  = "gemma-3-4b-it"
CALL_DELAY  = 4   # saniye

DATA_FILE   = ROOT / "backend" / "seed" / "test_reports.json"

# Test için kullanılacak rapor indeksleri (10 rapor)
REPORT_INDICES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45]

# ── Göstergeler ────────────────────────────────────────────────────────────
ALL_INDICATORS = [
    "people_trapped", "people_injured", "life_threat", "children_elderly_at_risk",
    "large_crowd_affected", "building_collapsed", "building_damaged", "road_blocked",
    "utility_disrupted", "utility_dangerous", "flood_water_rising", "fire_active",
    "landslide_active", "hazmat_present", "aftershock_risk", "no_communication",
    "area_isolated", "rescue_requested",
]

# ── Sistem Promptu ──────────────────────────────────────────────────────────
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

PERTURBATION_NAMES = {
    "uppercase":  "Büyük Harf",
    "ascii":      "ASCII Türkçe",
    "typos":      "Yazım Hatası",
    "condensed":  "Sıkıştırılmış",
}

# ── Pertürbasyon Fonksiyonları ──────────────────────────────────────────────
def perturb_uppercase(text: str) -> str:
    """Tüm metni büyük harfe çevir."""
    return text.upper()


def perturb_ascii(text: str) -> str:
    """Türkçe karakterleri ASCII eşdeğeriyle değiştir (mobil klavye simülasyonu)."""
    tr_map = {
        "ğ": "g", "Ğ": "G", "ş": "s", "Ş": "S",
        "ı": "i", "İ": "I", "ü": "u", "Ü": "U",
        "ö": "o", "Ö": "O", "ç": "c", "Ç": "C",
    }
    for tr_ch, en_ch in tr_map.items():
        text = text.replace(tr_ch, en_ch)
    return text


def perturb_typos(text: str) -> str:
    """Yazım hatası simülasyonu: sesli harf atlatma ve harf tekrarı."""
    rng = random.Random(42)
    vowels = "aeıioöuüAEIİOÖUÜ"
    words = text.split()
    result = []
    for word in words:
        if len(word) > 4 and rng.random() < 0.35:
            chars = list(word)
            v_idx = [i for i, c in enumerate(chars) if c in vowels]
            if v_idx:
                chars.pop(rng.choice(v_idx))
                word = "".join(chars)
        if len(word) > 3 and rng.random() < 0.25:
            word = word + word[-1]
        result.append(word)
    return " ".join(result)


def perturb_condensed(text: str) -> str:
    """Noktalama kaldır; her 3 kelimeyi bitişik yaz."""
    text = re.sub(r"[.,!?;:\"'()]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    groups = []
    for i in range(0, len(words), 3):
        chunk = words[i: i + 3]
        groups.append("".join(chunk) if len(chunk) == 3 else " ".join(chunk))
    return " ".join(groups)


PERTURB_FNS = {
    "uppercase": perturb_uppercase,
    "ascii":     perturb_ascii,
    "typos":     perturb_typos,
    "condensed": perturb_condensed,
}

# ── Yardımcılar ─────────────────────────────────────────────────────────────
def extract_json(raw: str) -> dict | None:
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


def call_gemma(text: str) -> dict:
    """Gemma 3 4B ile gösterge çıkarımı — sistem promptu user mesajına merge edilir."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=API_KEY)
    merged = f"{SYSTEM_PROMPT}\n\nAşağıdaki afet bildirimi metnini analiz et:\n\n{text}"
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=merged,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        parsed = extract_json(response.text.strip())
        if parsed and "indicators" in parsed:
            return parsed["indicators"]
    except Exception as e:
        print(f"    ⚠️  API hatası: {e}")
    return {k: False for k in ALL_INDICATORS}


def jaccard(ind_a: dict, ind_b: dict) -> float:
    set_a = {k for k, v in ind_a.items() if v}
    set_b = {k for k, v in ind_b.items() if v}
    if not set_a and not set_b:
        return 1.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return round(intersection / union, 3) if union > 0 else 1.0


def flip_count(ind_a: dict, ind_b: dict) -> int:
    return sum(1 for k in ALL_INDICATORS if bool(ind_a.get(k)) != bool(ind_b.get(k)))


# ── Veri Yükleme ────────────────────────────────────────────────────────────
def load_reports() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    all_reports = data["raw_reports"]
    return [all_reports[i] for i in REPORT_INDICES if i < len(all_reports)]


# ── Tahmin Yükle / Çalıştır ─────────────────────────────────────────────────
def load_or_run_predictions(reports: list[dict]) -> dict:
    if CACHE_FILE.exists():
        print("💾  Önbellek bulundu, API çağrısı yapılmıyor.")
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)

    total_calls = len(reports) * (1 + len(PERTURB_FNS))
    print(f"🚀  {len(reports)} rapor × {len(PERTURB_FNS)+1} varyant = {total_calls} API çağrısı yapılıyor...")

    cache: dict = {}
    call_idx = 0

    for report in reports:
        rid   = report["report_id"]
        text  = report.get("event_define", "") or ""
        cache[rid] = {}

        # Orijinal
        call_idx += 1
        print(f"  [{call_idx:03d}/{total_calls}] {rid} | orijinal")
        cache[rid]["original"] = call_gemma(text)
        time.sleep(CALL_DELAY)

        # Pertürbasyonlar
        for ptype, pfn in PERTURB_FNS.items():
            call_idx += 1
            perturbed = pfn(text)
            print(f"  [{call_idx:03d}/{total_calls}] {rid} | {ptype}")
            cache[rid][ptype] = call_gemma(perturbed)
            time.sleep(CALL_DELAY)

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"✅  Önbellek kaydedildi: {CACHE_FILE}")
    return cache


# ── Metrik Hesaplama ─────────────────────────────────────────────────────────
def compute_metrics(reports: list[dict], cache: dict) -> pd.DataFrame:
    rows = []
    for report in reports:
        rid  = report["report_id"]
        text = (report.get("event_define", "") or "")[:60]
        orig = cache.get(rid, {}).get("original", {k: False for k in ALL_INDICATORS})
        row = {
            "report_id":    rid,
            "text_preview": text,
        }
        for ptype in PERTURB_FNS:
            pred = cache.get(rid, {}).get(ptype, {k: False for k in ALL_INDICATORS})
            row[f"{ptype}_jaccard"] = jaccard(orig, pred)
            row[f"{ptype}_flips"]   = flip_count(orig, pred)
        jac_vals = [row[f"{pt}_jaccard"] for pt in PERTURB_FNS]
        row["mean_jaccard"] = round(np.mean(jac_vals), 3)
        row["min_jaccard"]  = round(np.min(jac_vals), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def compute_flip_matrix(reports: list[dict], cache: dict) -> pd.Series:
    """Her gösterge için toplam flip sayısı (tüm raporlar × tüm pertürbasyonlar)."""
    flips = {k: 0 for k in ALL_INDICATORS}
    for report in reports:
        rid  = report["report_id"]
        orig = cache.get(rid, {}).get("original", {k: False for k in ALL_INDICATORS})
        for ptype in PERTURB_FNS:
            pred = cache.get(rid, {}).get(ptype, {k: False for k in ALL_INDICATORS})
            for ind in ALL_INDICATORS:
                if bool(orig.get(ind)) != bool(pred.get(ind)):
                    flips[ind] += 1
    return pd.Series(flips).sort_values(ascending=False)


# ── Plot 1: Kutu Grafiği ─────────────────────────────────────────────────────
def plot_stability_boxplot(df: pd.DataFrame):
    data_per_type = {
        PERTURBATION_NAMES[pt]: df[f"{pt}_jaccard"].tolist()
        for pt in PERTURB_FNS
    }
    labels = list(data_per_type.keys())
    values = list(data_per_type.values())
    colors = ["#4393c3", "#d6604d", "#74c476", "#f4a460"]

    _, ax = plt.subplots(figsize=(10, 6))
    bp = ax.boxplot(values, patch_artist=True, labels=labels, widths=0.5,
                    medianprops=dict(color="black", linewidth=2))
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)

    # Dağılım noktaları (jitter)
    rng = np.random.default_rng(42)
    for i, (vals, color) in enumerate(zip(values, colors), 1):
        jitter = rng.uniform(-0.12, 0.12, len(vals))
        ax.scatter([i + j for j in jitter], vals, color=color,
                   alpha=0.85, s=55, zorder=3, edgecolors="white", linewidth=0.5)

    overall_mean = np.mean([v for vlist in values for v in vlist])
    ax.axhline(overall_mean, color="gray", linestyle="--", linewidth=1.2,
               label=f"Genel Ortalama: {overall_mean:.3f}")
    ax.axhline(1.0, color="#1a9850", linestyle=":", linewidth=1.0, alpha=0.5)

    ax.set_ylim(-0.05, 1.15)
    ax.set_ylabel("Jaccard Benzerliği (Orijinal ile)", fontsize=12)
    ax.set_title(
        "Pertürbasyon Türüne Göre Jaccard Benzerliği\n"
        f"Model: {MODEL_NAME}  |  N={len(df)} rapor  |  1.0 = tam eşleşme",
        fontsize=12, pad=10,
    )
    ax.legend(fontsize=9)
    plt.tight_layout()
    out = OUTPUT_DIR / "pr_stability_boxplot.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Plot 2: Isı Haritası ─────────────────────────────────────────────────────
def plot_report_heatmap(df: pd.DataFrame):
    heat_data = df.set_index("report_id")[
        [f"{pt}_jaccard" for pt in PERTURB_FNS]
    ].rename(columns={f"{pt}_jaccard": PERTURBATION_NAMES[pt] for pt in PERTURB_FNS})

    _, ax = plt.subplots(figsize=(8, max(6, len(df) * 0.55)))
    sns.heatmap(
        heat_data, annot=True, fmt=".2f", cmap="RdYlGn",
        vmin=0.0, vmax=1.0, linewidths=0.5, ax=ax,
        cbar_kws={"label": "Jaccard Benzerliği (0–1)"},
    )
    ax.set_title(
        "Rapor × Pertürbasyon Jaccard Isı Haritası\n"
        f"Model: {MODEL_NAME}  |  1.0 = tam eşleşme, 0.0 = tam ayrışma",
        fontsize=11, pad=10,
    )
    ax.set_xlabel("Pertürbasyon Türü", fontsize=10)
    ax.set_ylabel("Rapor", fontsize=10)
    plt.tight_layout()
    out = OUTPUT_DIR / "pr_report_heatmap.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Plot 3: Flip Frekansı ────────────────────────────────────────────────────
def plot_flip_frequency(flip_series: pd.Series):
    max_possible = len(REPORT_INDICES) * len(PERTURB_FNS)
    colors = [
        "#d73027" if v > 5 else "#fee08b" if v >= 2 else "#1a9850"
        for v in flip_series.values
    ]

    _, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(flip_series.index, flip_series.values, color=colors, edgecolor="white", width=0.7)
    ax.axhline(flip_series.mean(), color="#2c7bb6", linestyle="--", linewidth=1.5,
               label=f"Ortalama: {flip_series.mean():.1f}")

    for bar, val in zip(bars, flip_series.values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                str(val), ha="center", va="bottom", fontsize=8.5, fontweight="bold")

    patches = [
        mpatches.Patch(color="#d73027", label="> 5 değişim (düşük sağlamlık)"),
        mpatches.Patch(color="#fee08b", label="2–5 değişim (orta sağlamlık)"),
        mpatches.Patch(color="#1a9850", label="≤ 1 değişim (yüksek sağlamlık)"),
    ]
    ax.legend(handles=patches + [ax.get_lines()[0]], fontsize=9, loc="upper right")
    ax.set_ylabel("Toplam Değişim Sayısı", fontsize=11)
    ax.set_xlabel("Gösterge", fontsize=11)
    ax.set_title(
        "Gösterge Başına Toplam Değişim Sayısı\n"
        f"4 pertürbasyon × {len(REPORT_INDICES)} rapor = maks {max_possible} değişim mümkün",
        fontsize=12, pad=10,
    )
    plt.xticks(rotation=40, ha="right", fontsize=9)
    plt.tight_layout()
    out = OUTPUT_DIR / "pr_flip_frequency.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Ana Akış ─────────────────────────────────────────────────────────────────
def main():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.grid": True,
        "grid.alpha": 0.3,
        "figure.facecolor": "white",
    })

    print("📡  Raporlar yükleniyor...")
    reports = load_reports()
    print(f"  → {len(reports)} rapor seçildi (indisler: {REPORT_INDICES})")

    cache = load_or_run_predictions(reports)

    print("\n📊  Metrikler hesaplanıyor...")
    df = compute_metrics(reports, cache)
    flip_series = compute_flip_matrix(reports, cache)

    # CSV
    csv_cols = ["report_id", "text_preview"] + \
               [f"{pt}_jaccard" for pt in PERTURB_FNS] + \
               ["mean_jaccard", "min_jaccard"]
    csv_path = OUTPUT_DIR / "pr_summary.csv"
    df[csv_cols].to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"💾  {csv_path}")

    plot_stability_boxplot(df)
    plot_report_heatmap(df)
    plot_flip_frequency(flip_series)

    # Özet
    print("\n" + "─" * 55)
    for pt in PERTURB_FNS:
        mean_j = df[f"{pt}_jaccard"].mean()
        print(f"{PERTURBATION_NAMES[pt]:<20}: Ort. Jaccard = {mean_j:.3f}")
    print(f"\n{'Genel Ortalama':<20}: {df['mean_jaccard'].mean():.3f}")
    worst_ind = flip_series.idxmax()
    print(f"En çok değişen gösterge : {worst_ind} ({flip_series[worst_ind]} kez)")
    best_ind = flip_series.idxmin()
    print(f"En stabil gösterge      : {best_ind} ({flip_series[best_ind]} kez)")
    print("─" * 55)
    print(f"\n✅  Tüm çıktılar: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
