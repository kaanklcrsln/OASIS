"""
Known-Answer Tests (KAT) — AI Sınıflandırıcı Doğruluk Analizi
==============================================================
Uzman etiketi gerektirmeyen, cevabı metin içeriğinden açıkça belli
30 senaryo ile Gemini 2.5 Flash indicator çıkarım doğruluğunu ölçer.

Yöntem:
  Her test senaryosu için beklenen boolean gösterge değerleri dilbilimsel
  analiz ile belirlendi (domain uzmanlığı gerektirmez — "mahsur kaldım"
  yazan biri mahsur kalmıştır). Model çıktısı beklenen ile karşılaştırılarak
  her gösterge için F1, Precision, Recall hesaplanır.

Bağımlılıklar:
  pip install google-genai python-dotenv pandas matplotlib seaborn

Çalıştırma:
  cd analysis && python known-answer.py

Çıktılar:
  outputs/kat_accuracy_bar.png       — indicator başına doğruluk çubuğu
  outputs/kat_f1_heatmap.png         — Precision / Recall / F1 ısı haritası
  outputs/kat_pr_scatter.png         — Precision-Recall scatter
  outputs/kat_summary.csv            — tam sonuç tablosu
"""

import os, sys, json, time, re
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from dotenv import load_dotenv

# ── Ortam ───────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_BACKUP")
if not API_KEY:
    sys.exit("❌  GEMINI_API_KEY bulunamadı. .env dosyasını kontrol edin.")

OUTPUT_DIR = Path(__file__).parent / "outputs"
CACHE_DIR  = Path(__file__).parent / ".cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

CACHE_FILE  = CACHE_DIR / "kat_predictions_gemma4b.json"
MODEL_NAME  = "gemma-3-4b-it"
CALL_DELAY  = 3  # saniye — rate limit koruması

# ── Tüm Gösterge Anahtarları ─────────────────────────────────────
ALL_INDICATORS = [
    "people_trapped", "people_injured", "life_threat", "children_elderly_at_risk",
    "large_crowd_affected", "building_collapsed", "building_damaged", "road_blocked",
    "utility_disrupted", "utility_dangerous", "flood_water_rising", "fire_active",
    "landslide_active", "hazmat_present", "aftershock_risk", "no_communication",
    "area_isolated", "rescue_requested",
]

# ── Sistem Promptu (üretimle aynı yapı) ─────────────────────────
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


def _mk(true_keys: list[str]) -> dict:
    """Sadece belirtilen anahtarları True, geri kalanını False yap."""
    base = {k: False for k in ALL_INDICATORS}
    for k in true_keys:
        base[k] = True
    return base


# ── 30 Test Senaryosu ────────────────────────────────────────────
# Her senaryo için beklenen değerler dilbilimsel analiz ile belirlendi.
# Kuralı basit: metinde açıkça yazılan bir durum için True, yazılmayan için False.
KNOWN_ANSWER_TESTS = [
    # ── GRUP 1: Mahsur Kalma ──────────────────────────────────────
    {"id": "kat-001", "group": "Mahsur", "difficulty": "easy",
     "text": "Binada mahsurum, kapılar kilitli, çıkamıyorum. Acil yardım gerekiyor.",
     "expected": _mk(["people_trapped", "rescue_requested"])},

    {"id": "kat-002", "group": "Mahsur", "difficulty": "easy",
     "text": "Araç içindeyiz, sel suyu cam hizasına geldi, kapılar açılmıyor. Boğulacağız.",
     "expected": _mk(["people_trapped", "life_threat", "flood_water_rising", "rescue_requested"])},

    {"id": "kat-003", "group": "Mahsur", "difficulty": "easy",
     "text": "Enkaz altındayım, bağırıyorum ama kimse duymuyor. Kurtarın beni lütfen.",
     "expected": _mk(["people_trapped", "life_threat", "rescue_requested", "building_collapsed"])},

    {"id": "kat-004", "group": "Mahsur", "difficulty": "medium",
     "text": "Mahallede yaşlı ve çocuklar var, sel nedeniyle tahliye edilemiyorlar.",
     "expected": _mk(["children_elderly_at_risk", "people_trapped", "flood_water_rising"])},

    {"id": "kat-005", "group": "Mahsur", "difficulty": "medium",
     "text": "Evden çıkamıyoruz, sokak tamamen sel suyu ile dolu, hiçbir yere ulaşamıyoruz.",
     "expected": _mk(["people_trapped", "flood_water_rising", "no_communication"])},

    # ── GRUP 2: Altyapı ───────────────────────────────────────────
    {"id": "kat-006", "group": "Altyapı", "difficulty": "easy",
     "text": "Heyelan nedeniyle Atatürk Caddesi tamamen kapandı, hiçbir araç geçemiyor.",
     "expected": _mk(["road_blocked", "landslide_active"])},

    {"id": "kat-007", "group": "Altyapı", "difficulty": "easy",
     "text": "Merkezdeki ana köprü çöktü, karşı yakaya kara yolu bağlantısı tamamen kesildi.",
     "expected": _mk(["road_blocked", "building_collapsed", "area_isolated"])},

    {"id": "kat-008", "group": "Altyapı", "difficulty": "easy",
     "text": "Elektrik ve su kesintisi var, deprem sonrası artçı sarsıntılar hissediliyor.",
     "expected": _mk(["utility_disrupted", "aftershock_risk"])},

    {"id": "kat-009", "group": "Altyapı", "difficulty": "easy",
     "text": "Elektrik telleri kopup suyun içine düştü, bölgeye yaklaşmayın, çarpma tehlikesi var.",
     "expected": _mk(["utility_dangerous", "life_threat"])},

    {"id": "kat-010", "group": "Altyapı", "difficulty": "easy",
     "text": "Bina duvarlarda derin çatlaklar oluştu, yapı tehlikeli görünüyor ama ayakta duruyor.",
     "expected": _mk(["building_damaged"])},

    # ── GRUP 3: Doğal Afet ────────────────────────────────────────
    {"id": "kat-011", "group": "Doğal Afet", "difficulty": "easy",
     "text": "Dere taştı, su seviyesi hızla yükseliyor, köye giren tek yol kapandı.",
     "expected": _mk(["flood_water_rising", "road_blocked", "area_isolated"])},

    {"id": "kat-012", "group": "Doğal Afet", "difficulty": "easy",
     "text": "Yangın hızla yayılıyor, alevler komşu binalara sıçradı.",
     "expected": _mk(["fire_active", "life_threat"])},

    {"id": "kat-013", "group": "Doğal Afet", "difficulty": "easy",
     "text": "Heyelan aktif, toprak kayıyor, 3 ev hasar gördü.",
     "expected": _mk(["landslide_active", "building_damaged"])},

    {"id": "kat-014", "group": "Doğal Afet", "difficulty": "easy",
     "text": "Gaz sızıntısı tespit edildi, kokunun geldiği yerde patlama riski var.",
     "expected": _mk(["hazmat_present", "life_threat"])},

    {"id": "kat-015", "group": "Doğal Afet", "difficulty": "medium",
     "text": "Büyük deprem oldu, çok sayıda bina yıkıldı, artçı sarsıntılar devam ediyor.",
     "expected": _mk(["building_collapsed", "large_crowd_affected", "aftershock_risk"])},

    # ── GRUP 4: Yaralanma / Hayati Risk ───────────────────────────
    {"id": "kat-016", "group": "Yaralanma", "difficulty": "easy",
     "text": "Yaralılar var, bacak kırıkları mevcut, ambulans gönderilmesi gerekiyor.",
     "expected": _mk(["people_injured", "rescue_requested"])},

    {"id": "kat-017", "group": "Yaralanma", "difficulty": "medium",
     "text": "İnsanlar elektriğe çarptı, bilinç kayıpları var, acil tıp ekibi gelsin.",
     "expected": _mk(["people_injured", "utility_dangerous", "life_threat", "rescue_requested"])},

    {"id": "kat-018", "group": "Yaralanma", "difficulty": "easy",
     "text": "Yardım gelmezse buradakiler ölecek. Çok acil, kurtarın.",
     "expected": _mk(["life_threat", "rescue_requested"])},

    {"id": "kat-019", "group": "Yaralanma", "difficulty": "medium",
     "text": "Su baskını çok büyük, tüm mahalle etkilendi, onlarca hane sular altında.",
     "expected": _mk(["flood_water_rising", "large_crowd_affected", "area_isolated"])},

    {"id": "kat-020", "group": "Yaralanma", "difficulty": "medium",
     "text": "Bölgeye tüm yollar kapandı, köy tamamen izole, hiçbir araç giremez.",
     "expected": _mk(["area_isolated", "road_blocked"])},

    # ── GRUP 5: Olumsuz (Afet Yok) ───────────────────────────────
    {"id": "kat-021", "group": "Olumsuz", "difficulty": "easy",
     "text": "Hava bugün çok güzel, ailecek parka çıktık.",
     "expected": _mk([])},

    {"id": "kat-022", "group": "Olumsuz", "difficulty": "easy",
     "text": "Alışveriş merkezi kalabalık, trafik biraz yoğun ama her şey normal.",
     "expected": _mk([])},

    {"id": "kat-023", "group": "Olumsuz", "difficulty": "easy",
     "text": "Dün hafif yağmur yağdı, bahçe sulandı, bugün bulutlu ama durum normal.",
     "expected": _mk([])},

    {"id": "kat-024", "group": "Olumsuz", "difficulty": "medium",
     "text": "Komşumun arabası arıza yaptı, yedek parça bekliyoruz.",
     "expected": _mk([])},

    {"id": "kat-025", "group": "Olumsuz", "difficulty": "easy",
     "text": "Bir dernek afet bilinci etkinliği düzenledi, toplantı çok verimli geçti.",
     "expected": _mk([])},

    # ── GRUP 6: Informal / Yazım Hatalı ──────────────────────────
    {"id": "kat-026", "group": "Informal", "difficulty": "hard",
     "text": "araba stop etı su gırıo içeri Kapı acılmıo yardım pls bogulucaz",
     "expected": _mk(["people_trapped", "life_threat", "flood_water_rising", "rescue_requested"])},

    {"id": "kat-027", "group": "Informal", "difficulty": "hard",
     "text": "trafo patlıo kımı buraa gelmesın elektırık carpıcak herkezı",
     "expected": _mk(["utility_dangerous", "life_threat"])},

    {"id": "kat-028", "group": "Informal", "difficulty": "hard",
     "text": "bina coktu heryer enkaz ıcınde bırı varmı kurtarın onları",
     "expected": _mk(["building_collapsed", "people_trapped", "rescue_requested"])},

    {"id": "kat-029", "group": "Informal", "difficulty": "hard",
     "text": "kopekler catıda kaldı su cok yukseldı kımse ALAMIYOO yardım edın ltfnnn",
     "expected": _mk(["flood_water_rising", "rescue_requested"])},

    {"id": "kat-030", "group": "Informal", "difficulty": "hard",
     "text": "catı coktu ustumze duvar yıkıldıı altında kaldıkkk kurtarınn bıziii",
     "expected": _mk(["building_collapsed", "people_trapped", "life_threat", "rescue_requested"])},
]


# ── API Çağrısı ──────────────────────────────────────────────────
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


def call_gemma(text: str) -> dict | None:
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
        return extract_json(response.text.strip())
    except Exception as e:
        print(f"  ⚠️  API hatası: {e}")
        return None


# ── Tahmin Yükle / Çalıştır ──────────────────────────────────────
def load_or_run_predictions() -> dict:
    if CACHE_FILE.exists():
        print("💾  Cache bulundu, API çağrısı yapılmıyor.")
        with open(CACHE_FILE) as f:
            return json.load(f)

    print(f"🚀  {len(KNOWN_ANSWER_TESTS)} senaryo için Gemini API çağrılıyor...")
    predictions = {}
    for i, test in enumerate(KNOWN_ANSWER_TESTS, 1):
        print(f"  [{i:02d}/{len(KNOWN_ANSWER_TESTS)}] {test['id']} — {test['text'][:55]}...")
        result = call_gemma(test["text"])
        if result and "indicators" in result:
            predictions[test["id"]] = result["indicators"]
        else:
            predictions[test["id"]] = {k: False for k in ALL_INDICATORS}
            print(f"    ⚠️  Geçersiz yanıt, tümü False olarak kaydedildi")
        time.sleep(CALL_DELAY)

    with open(CACHE_FILE, "w") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
    print(f"✅  Cache kaydedildi: {CACHE_FILE}")
    return predictions


# ── Metrik Hesaplama ─────────────────────────────────────────────
def compute_metrics(predictions: dict) -> pd.DataFrame:
    rows = []
    for ind in ALL_INDICATORS:
        tp = fp = fn = tn = 0
        for test in KNOWN_ANSWER_TESTS:
            expected = test["expected"][ind]
            predicted = bool(predictions.get(test["id"], {}).get(ind, False))
            if expected and predicted:
                tp += 1
            elif not expected and predicted:
                fp += 1
            elif expected and not predicted:
                fn += 1
            else:
                tn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        accuracy  = (tp + tn) / len(KNOWN_ANSWER_TESTS)

        rows.append({
            "indicator": ind, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3), "accuracy": round(accuracy, 3),
        })
    return pd.DataFrame(rows).set_index("indicator")


# ── Plot 1: Doğruluk Çubuğu ─────────────────────────────────────
def plot_accuracy_bar(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(13, 6))
    sorted_df = df.sort_values("accuracy", ascending=True)
    colors = ["#d73027" if v < 0.7 else "#fee08b" if v < 0.85 else "#1a9850"
              for v in sorted_df["accuracy"]]
    bars = ax.barh(sorted_df.index, sorted_df["accuracy"], color=colors, edgecolor="white", height=0.7)
    ax.axvline(x=sorted_df["accuracy"].mean(), color="#2c7bb6", linestyle="--",
               linewidth=1.5, label=f"Ortalama: {sorted_df['accuracy'].mean():.2%}")
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Doğruluk (Accuracy)", fontsize=12)
    ax.set_title(f"Gösterge Başına Sınıflandırma Doğruluğu\nModel: {MODEL_NAME}  |  N={len(KNOWN_ANSWER_TESTS)} senaryo",
                 fontsize=13, pad=12)
    for bar, val in zip(bars, sorted_df["accuracy"]):
        ax.text(val + 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:.0%}", va="center", fontsize=9)
    patches = [
        mpatches.Patch(color="#1a9850", label="≥ 85%"),
        mpatches.Patch(color="#fee08b", label="70–84%"),
        mpatches.Patch(color="#d73027", label="< 70%"),
    ]
    ax.legend(handles=patches + [ax.get_lines()[0]], loc="lower right", fontsize=9)
    plt.tight_layout()
    out = OUTPUT_DIR / "kat_accuracy_bar.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Plot 2: F1 / Precision / Recall Isı Haritası ─────────────────
def plot_f1_heatmap(df: pd.DataFrame):
    heat_df = df[["precision", "recall", "f1"]].rename(
        columns={"precision": "Precision", "recall": "Recall", "f1": "F1 Score"})
    heat_df = heat_df.sort_values("F1 Score", ascending=False)

    fig, ax = plt.subplots(figsize=(7, 10))
    sns.heatmap(heat_df, annot=True, fmt=".2f", cmap="RdYlGn",
                vmin=0, vmax=1, linewidths=0.5, ax=ax,
                cbar_kws={"label": "Skor (0–1)"})
    ax.set_title(f"Precision / Recall / F1 Isı Haritası\nModel: {MODEL_NAME}  |  N={len(KNOWN_ANSWER_TESTS)}",
                 fontsize=12, pad=10)
    ax.set_xlabel("")
    plt.tight_layout()
    out = OUTPUT_DIR / "kat_f1_heatmap.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Plot 3: Precision–Recall Scatter ─────────────────────────────
def plot_pr_scatter(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(8, 7))
    sc = ax.scatter(df["recall"], df["precision"],
                    c=df["f1"], cmap="RdYlGn", s=120, vmin=0, vmax=1, zorder=3)
    for ind, row in df.iterrows():
        ax.annotate(ind, (row["recall"], row["precision"]),
                    textcoords="offset points", xytext=(6, 3), fontsize=7.5)
    ax.axhline(0.8, color="gray", linestyle=":", linewidth=0.8)
    ax.axvline(0.8, color="gray", linestyle=":", linewidth=0.8)
    ax.set_xlim(-0.05, 1.1)
    ax.set_ylim(-0.05, 1.1)
    ax.set_xlabel("Recall (Duyarlılık)", fontsize=12)
    ax.set_ylabel("Precision (Kesinlik)", fontsize=12)
    ax.set_title(f"Precision–Recall Dağılımı\nModel: {MODEL_NAME}  |  N={len(KNOWN_ANSWER_TESTS)} senaryo",
                 fontsize=12, pad=10)
    cbar = plt.colorbar(sc, ax=ax)
    cbar.set_label("F1 Score", fontsize=10)
    ax.text(0.82, 0.03, "Yüksek Recall,\nDüşük Precision", fontsize=8, color="gray")
    ax.text(0.02, 0.82, "Düşük Recall,\nYüksek Precision", fontsize=8, color="gray")
    ax.text(0.82, 0.82, "İdeal Bölge", fontsize=8, color="#1a9850",
            fontweight="bold")
    plt.tight_layout()
    out = OUTPUT_DIR / "kat_pr_scatter.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"💾  {out}")


# ── Ana Akış ─────────────────────────────────────────────────────
def main():
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True,
                         "grid.alpha": 0.3, "figure.facecolor": "white"})

    predictions = load_or_run_predictions()
    df = compute_metrics(predictions)

    # CSV
    csv_path = OUTPUT_DIR / "kat_summary.csv"
    df.to_csv(csv_path)
    print(f"💾  {csv_path}")

    plot_accuracy_bar(df)
    plot_f1_heatmap(df)
    plot_pr_scatter(df)

    overall_acc = df["accuracy"].mean()
    overall_f1  = df["f1"].mean()
    positive_f1 = df[df[["TP", "FN"]].sum(axis=1) > 0]["f1"].mean()
    print("\n" + "─" * 50)
    print(f"Ortalama Doğruluk  : {overall_acc:.2%}")
    print(f"Ortalama F1        : {overall_f1:.3f}")
    print(f"Pozitif Sınıf F1   : {positive_f1:.3f}  (en az 1 beklenen True olan göstergeler)")
    print(f"Toplam Senaryo     : {len(KNOWN_ANSWER_TESTS)}")
    print(f"Model              : {MODEL_NAME}")
    print("─" * 50)
    print(f"\nTüm çıktılar: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
