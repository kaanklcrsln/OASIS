#!/usr/bin/env python3
"""
OASIS — test_reports.json batch AI işleme scripti.

test_reports.json'daki 50 ham raporu Gemma 4 31B ile analiz eder
(token/quota hatası durumunda Gemma 3 27B → Gemma 3 12B sırasıyla fallback),
sonuçları test_reports_processed.json olarak yazar.

Kullanım:  python scripts/process_test_reports.py
API Key:   .env → GEMINI_API_KEY  (ve opsiyonel GEMINI_API_KEY_BACKUP)
"""

import os
import re
import sys
import json
import time
from pathlib import Path

# ── Ayarlar ──────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
INPUT_FILE  = ROOT / "backend" / "seed" / "test_reports.json"
OUTPUT_FILE = ROOT / "data" / "test_reports_processed.json"
ENV_FILE    = ROOT / ".env"

PRIMARY_MODEL   = "gemma-4-31b-it"
FALLBACK_MODELS = ["gemma-3-27b-it", "gemma-3-12b-it"]
PRE_CALL_DELAY  = 4   # saniye (her çağrı öncesi)
QUOTA_BACKOFF   = 15  # saniye (quota hatası sonrası)

ALL_INDICATOR_KEYS = [
    "people_trapped",
    "people_injured",
    "life_threat",
    "children_elderly_at_risk",
    "large_crowd_affected",
    "building_collapsed",
    "building_damaged",
    "road_blocked",
    "utility_disrupted",
    "utility_dangerous",
    "flood_water_rising",
    "fire_active",
    "landslide_active",
    "hazmat_present",
    "aftershock_risk",
    "no_communication",
    "area_isolated",
    "rescue_requested",
]

_INDICATOR_SCHEMA = ",\n    ".join([f'"{k}": boolean' for k in ALL_INDICATOR_KEYS])

SYSTEM_PROMPT = """Sen bir Afet Karar Destek Sistemi yapay zekâsısın.
Bir sohbet botu değilsin, karar destek bileşenisin.

Görevin:
• Kullanıcıdan gelen METİN verisini analiz etmek
• Afet türünü belirlemek
• Durumu gözlemlere dayalı BOOLEAN (true/false) göstergelerle raporlamak
• Sayısal aciliyet puanı VERME — sadece gözlemlerini boolean olarak bildir

KESİN KURALLAR:
• Asla serbest metin yazma, SADECE JSON üret
• Emin olmadığın göstergeleri false yap (güvenli taraf)
• Çelişkili verilerde İNSAN HAYATINI öncele → true yap
• Şemada olmayan alan ekleme

GÖSTERGE KARARLARI İÇİN KILAVUZ:
• people_trapped: "Mahsur kaldım", "Çıkamıyoruz", "Enkaz altındayım" → true
• people_injured: "Yaralılar var", "Kanama", "Kırık" → true
• life_threat: "Ölüyoruz", "Yardım gelmezse...", "Tehlike altındayız" → true
• children_elderly_at_risk: "Çocuklar var", "Yaşlı", "Bebek" → true
• large_crowd_affected: "Mahalle/köy etkilendi", "Çok kişi", "Onlarca" → true
• building_collapsed: "Bina çöktü", "Yıkıldı", "Enkaz" → true
• building_damaged: "Çatlaklar", "Duvar yıkıldı", orta hasar → true
• road_blocked: "Yol kapandı", "Ulaşım yok", "Geçilemiyor" → true
• utility_disrupted: "Elektrik/su/gaz kesildi" → true
• utility_dangerous: "Gaz sızıntısı", "Elektrik çarpma tehlikesi" → true
• flood_water_rising: "Su yükseliyor", "Su basıyor", "Dere taştı" → true
• fire_active: "Yangın var", "Yanıyor", "Duman" → true
• landslide_active: "Toprak kayması", "Heyelan" → true
• hazmat_present: "Kimyasal sızıntı", "Tehlikeli madde" → true
• aftershock_risk: "Artçı depremler", "Deprem sonrası" → true
• no_communication: "Sinyal yok", "İletişim kesildi" → true
• area_isolated: "Yol yok", "Ulaşılamıyor", "İzole" → true
• rescue_requested: "Kurtarma istiyoruz", "Yardım gönderin" → true

ÇIKTI DİLİ: Türkçe
SADECE JSON üret — hiçbir ek metin/markdown yazma.

JSON ŞEMASI:
{
  "incident_id": "string",
  "report_date": "ISO 8601",
  "data_quality": "high | low",
  "disaster_type": "string (sel, heyelan, deprem, yangın, fırtına, kar vb.)",
  "disaster_subtype": "string | null",
  "indicators": {
    """ + _INDICATOR_SCHEMA + """
  },
  "confidence_score": 0.0-1.0,
  "summary": "string (kısa Türkçe özet, 1-2 cümle)",
  "location_description": "string | null"
}"""


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _extract_json(raw: str):
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


def _determine_user_status(indicators: dict, can_communicate: bool) -> str:
    """Basit kural: life_threat + people_trapped + can_communicate=False → kritik durum."""
    if (
        indicators.get("life_threat")
        and indicators.get("people_trapped")
        and not can_communicate
    ):
        return "entrapment-life-threatening-risk"
    return ""


def _call_llm(api_keys: list, report: dict) -> dict | None:
    """
    Raporu Gemma 4 31B ile analiz et.
    Quota/rate hatası durumunda fallback modellere geç.
    Her model için tüm API key'ler denenir.
    """
    from google import genai
    from google.genai import types

    lat = report["gps_location"]["coordinates"][1]
    lon = report["gps_location"]["coordinates"][0]

    user_message = (
        f"Aşağıdaki afet ihbarını analiz et ve boolean göstergelerle JSON çıktısı üret.\n\n"
        f"incident_id: {report['report_id']}\n"
        f"Kullanıcı tipi: {report['user_behavior']}\n"
        f"İletişim kurabilir mi: {report['can_communicate']}\n"
        f"Konum (lat, lon): {lat}, {lon}\n"
        f"Mesaj: {report['event_define'] or 'Açıklama girilmedi'}"
    )

    model_queue = [PRIMARY_MODEL] + FALLBACK_MODELS

    for model in model_queue:
        for i, api_key in enumerate(api_keys):
            try:
                client = genai.Client(api_key=api_key)
                merged = f"{SYSTEM_PROMPT}\n\n{user_message}"
                response = client.models.generate_content(
                    model=model,
                    contents=[merged],
                    config=types.GenerateContentConfig(temperature=0.1),
                )
                raw_text = response.text.strip()
                parsed = _extract_json(raw_text)
                if parsed:
                    print(f"  ✅ {model} (key #{i+1})")
                    return parsed
                else:
                    print(f"  ⚠️  {model} JSON parse hatası, raw: {raw_text[:120]}")
                    return None
            except Exception as e:
                err = str(e).lower()
                if "quota" in err or "rate" in err or "429" in err or "resource_exhausted" in err:
                    print(f"  ⚠️  {model} key #{i+1} limit → bekleniyor ({QUOTA_BACKOFF}s)...")
                    time.sleep(QUOTA_BACKOFF)
                    continue
                else:
                    print(f"  ❌ {model} beklenmeyen hata: {e}")
                    raise

        if model != model_queue[-1]:
            print(f"  ↩️  {model} tüm key'ler tükendi → {model_queue[model_queue.index(model)+1]}'a geçiliyor")

    print("  ❌ Tüm modeller ve key'ler tükendi")
    return None


# ── Ana akış ─────────────────────────────────────────────────────────────────

def main():
    # API key'leri yükle
    _env = _load_env(ENV_FILE)
    api_keys = [
        k for k in [
            os.getenv("GEMINI_API_KEY") or _env.get("GEMINI_API_KEY", ""),
            os.getenv("GEMINI_API_KEY_BACKUP") or _env.get("GEMINI_API_KEY_BACKUP", ""),
        ]
        if k
    ]
    if not api_keys:
        sys.exit("❌ GEMINI_API_KEY bulunamadı. .env dosyasını kontrol edin.")

    # Girdi dosyasını yükle
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_reports = data["raw_reports"]
    print(f"📋 {len(raw_reports)} rapor yüklendu | Model: {PRIMARY_MODEL}\n")

    # Mevcut çıktıyı yükle (yarım kalan işlemi sürdür)
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
        processed_map = {r["report_id"]: r for r in existing.get("processed_reports", [])}
        print(f"♻️  Mevcut {len(processed_map)} işlenmiş rapor bulundu, devam ediliyor...\n")
    else:
        processed_map = {}

    processed_reports = []

    for idx, report in enumerate(raw_reports, 1):
        report_id = report["report_id"]
        print(f"[{idx:02d}/{len(raw_reports)}] {report_id}: {report['event_define'][:60]}...")

        # Daha önce işlendiyse atla
        if report_id in processed_map:
            print(f"  ⏭️  Zaten işlenmiş, atlanıyor\n")
            processed_reports.append(processed_map[report_id])
            continue

        time.sleep(PRE_CALL_DELAY)

        result = _call_llm(api_keys, report)

        if result:
            indicators_raw = result.get("indicators", {})
            # Sadece true olanları sakla
            indicators_json = {k: True for k, v in indicators_raw.items() if v is True}
            disaster_type = result.get("disaster_type", "")
            can_communicate = report["can_communicate"]
            user_status = _determine_user_status(indicators_json, can_communicate)
        else:
            indicators_json = {}
            disaster_type = ""
            user_status = ""

        processed = {
            "id": report["id"],
            "report_id": report_id,
            "user_behavior": report["user_behavior"],
            "can_communicate": report["can_communicate"],
            "gps_location": report["gps_location"],
            "event_define": report["event_define"],
            "event_capture": report["event_capture"],
            "image_path": report["image_path"],
            "image_location": report["image_location"],
            "image_date": report["image_date"],
            "report_date": report["report_date"],
            "created_at": report["created_at"],
            "disaster_type": disaster_type,
            "indicators_json": indicators_json,
            "user_status": user_status,
        }
        processed_reports.append(processed)
        processed_map[report_id] = processed

        active = [k for k, v in indicators_json.items() if v]
        print(f"  → disaster={disaster_type} | indicators={active} | status={user_status or '-'}\n")

        # Her rapordan sonra kaydet (crash güvenliği)
        output = {
            "region": data["region"],
            "processed_reports": processed_reports,
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ Tamamlandı! {len(processed_reports)} rapor → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
