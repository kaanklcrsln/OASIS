import os
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from google import genai
from google.genai import types

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME = "gemma-3-4B"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """Sen bir Afet Karar Destek Sistemi yapay zekâsısın.
Bir sohbet botu değilsin, karar destek bileşenisin.

Görevin:
• Kullanıcıdan gelen metin, fotoğraf verilerini analiz etmek
• Afet türünü, şiddetini ve aciliyetini belirlemek
• İnsan hayatını önceleyen, güvenli ve temkinli çıkarımlar yapmak
• Çıktıyı SADECE verilen Incident Report JSON şemasına uygun üretmek

KESİN KURALLAR:
• Asla serbest metin yazma
• Açıklama, yorum, başlık veya markdown ekleme
• Sadece geçerli JSON üret
• Şemada olmayan alan ekleme
• Emin olmadığın bilgileri tahmin etme, null bırak
• Tahmin yaptığında ilgili confidence alanlarını doldur
• Çelişkili verilerde güvenli tarafta kal

ÖNCELİK SIRASI:
1. İnsan hayatı riski
2. Aciliyet ve müdahale ihtiyacı
3. Veri güvenilirliği
4. Operasyonel uygulanabilirlik

ANALİZ ADIMLARI:
1. Olayın afet türünü ve varsa alt türünü belirle
2. Olayın şiddet seviyesini (1–5) ve aciliyetini belirle
3. İnsan etkisini analiz et:
   - Yaralı
   - Mahsur kalan
6. Altyapı durumunu değerlendir:
   - Bina hasarı
   - Yol/erişim durumu
   - Elektrik, su, gaz, internet kesintisi
7. Verinin güvenilirliğini değerlendir:
   - Konum ile metin uyumlu mu?
   - Medya tutarlı mı?
   - Benzer raporlar var mı?
8. En uygun aksiyonu seç:
   dispatch_rescue, dispatch_medical, verify, monitor, ignore

KARAR KURALLARI:
• Mahsur kalan kişi varsa → dispatch_rescue
• Yaralı varsa → dispatch_medical veya dispatch_rescue
• Veri şüpheli ama kritikse → needs_review
• Önemsiz ve güvenilmezse → ignore

YAZIM DÜZELTME KURALI:
• Mesaj veri_kalitesi "low" (Hatalı) ise önce mesajı düzgün Türkçeye çevir
• Düzeltilmiş metni "corrected_text" alanına yaz
• Düzeltilmiş metni analiz için kullan
• Mesaj veri_kalitesi "high" (Kaliteli) ise corrected_text null bırak

VR ODAKLI ÇIKTI:
• Yıkılmış bina, su basmış alan, kapalı yol gibi unsurları vr.objects altında belirt
• Gerekirse severity_level ekle

ÇIKTI FORMATI:
• ÇIKTI DİLİ: Türkçe
• SADECE JSON üret
• JSON, Incident Report şemasına %100 uyumlu olmalı

Unutma:
Bu sistem gerçek bir afet senaryosunda operatörlerin ve karar vericilerin hayat kurtarma sürecini destekler.

INCIDENT REPORT JSON ŞEMASI:
{
  "incident_id": "string (otomatik, ör: INC-001)",
  "report_date": "ISO 8601 datetime",
  "data_quality": "high | low",
  "disaster": {
    "type": "string (sel, heyelan, deprem, yangın, vb.)",
    "subtype": "string veya null",
    "severity_level": "integer 1-5",
    "urgency": "immediate | high | medium | low"
  },
  "location": {
    "coordinates": {
      "lat": "float",
      "lon": "float"
    },
    "description": "string veya null"
  },
  "human_impact": {
    "trapped_persons": "integer veya null",
    "injured_persons": "integer veya null",
    "life_risk": "boolean"
  },
  "infrastructure": {
    "building_damage": "none | minor | moderate | severe | null",
    "road_access": "open | partial | closed | null",
    "utilities": {
      "electricity": "ok | disrupted | dangerous | null",
      "water": "ok | disrupted | null",
      "gas": "ok | disrupted | dangerous | null",
      "internet": "ok | disrupted | null"
    }
  },
  "recommended_action": "dispatch_rescue | dispatch_medical | needs_review | monitor | ignore",
  "confidence_score": "float 0.0-1.0",
  "corrected_text": "string (Hatalı mesaj düzeltilmiş hali) veya null (Kaliteli mesajlarda)",
  "summary": "string (kısa Türkçe özet, düzeltilmiş metne göre)",
  "vr": {
    "objects": ["string listesi, ör: yıkılmış bina, su bazmış yol"],
    "severity_level": "integer 1-5 veya null"
  }
}"""

# ─────────────────────────────────────────────
# SENARYOLAR
# ─────────────────────────────────────────────
SCENARIOS = [
    # ── 1. Ulaşımın Kesilmesi ──────────────────────────────────────────
    {
        "category": "Ulaşımın Kesilmesi",
        "quality": "Kaliteli",
        "text": "Heyelan nedeniyle Uzungöl ana yolu kapandı, bölgeye araç girişi ve çıkışı şu an için yapılamıyor.",
        "lat": 40.6225, "lon": 40.2885,
    },
    {
        "category": "Ulaşımın Kesilmesi",
        "quality": "Kaliteli",
        "text": "Merkezdeki ana köprü çöktüğü için karşı yakayla olan kara yolu bağlantısı tamamen kesilmiş durumda.",
        "lat": 40.6184, "lon": 40.2915,
    },
    {
        "category": "Ulaşımın Kesilmesi",
        "quality": "Hatalı",
        "text": "yollar kapandi kısme gecemıo her yer tas toprak dolduu acil yol lazımmm.",
        "lat": 40.6225, "lon": 40.2885,
    },
    {
        "category": "Ulaşımın Kesilmesi",
        "quality": "Hatalı",
        "text": "asfalt koptu gıttı off araba gecmıo burdan mahsur kaldkk yardım.",
        "lat": 40.6150, "lon": 40.2980,
    },
    {
        "category": "Ulaşımın Kesilmesi",
        "quality": "Hatalı",
        "text": "yol coktu ucurum oldU sakın gelmıyn buraya yol yok sakınnn.",
        "lat": 40.6100, "lon": 40.3050,
    },
    # ── 2. Hayvanlar Mahsur ────────────────────────────────────────────
    {
        "category": "Hayvanlar Mahsur",
        "quality": "Kaliteli",
        "text": "Dere kenarındaki ahırı su bastı; hayvanları güvenli ve yüksek bir bölgeye tahliye etmek için acil desteğe ihtiyacımız var.",
        "lat": 40.6165, "lon": 40.2945,
    },
    {
        "category": "Hayvanlar Mahsur",
        "quality": "Kaliteli",
        "text": "Sel sularına kapılan ve sürüklenen sokak hayvanları için profesyonel ekiplerin botla müdahale etmesi gerekiyor.",
        "lat": 40.6195, "lon": 40.2925,
    },
    {
        "category": "Hayvanlar Mahsur",
        "quality": "Hatalı",
        "text": "kopekler catıda kaldı su cok yukseldı kımse ALAMIYOO yardım edın ltfnnn.",
        "lat": 40.6178, "lon": 40.2938,
    },
    {
        "category": "Hayvanlar Mahsur",
        "quality": "Hatalı",
        "text": "inekler bogulcak ahır su doldu kapı acılmıoo yardm nolur.",
        "lat": 40.6135, "lon": 40.2945,
    },
    {
        "category": "Hayvanlar Mahsur",
        "quality": "Hatalı",
        "text": "kedıler agacta mahsur su cok sert akıyo kımse gıdemıo yanına ölcek hayvanlar.",
        "lat": 40.6210, "lon": 40.2895,
    },
    # ── 3. Araç İçinde Mahsur Kalma ───────────────────────────────────
    {
        "category": "Araç İçinde Mahsur Kalma",
        "quality": "Kaliteli",
        "text": "Aracımız sel suları nedeniyle stop etti; su seviyesi cam hizasına kadar yükseldi, tahliye edilemiyoruz.",
        "lat": 40.6190, "lon": 40.2905,
    },
    {
        "category": "Araç İçinde Mahsur Kalma",
        "quality": "Kaliteli",
        "text": "Otomobilimiz balçığa saplandı ve kapılar dışarıdaki su basıncından dolayı açılmıyor, araç içinde kilitli kaldık.",
        "lat": 40.6205, "lon": 40.2875,
    },
    {
        "category": "Araç İçinde Mahsur Kalma",
        "quality": "Hatalı",
        "text": "araba stop etı su gırıo içeri Kapı acılmıo yardım pls bogulucaz.",
        "lat": 40.6170, "lon": 40.2955,
    },
    {
        "category": "Araç İçinde Mahsur Kalma",
        "quality": "Hatalı",
        "text": "arabanın ıcındeyız su yukselıyoOO cok korkuozzz acıl glın kurtarın bızı.",
        "lat": 40.6160, "lon": 40.2970,
    },
    {
        "category": "Araç İçinde Mahsur Kalma",
        "quality": "Hatalı",
        "text": "camı kıramıoz su doluyo arabaa suruklenıo imdattt yardım.",
        "lat": 40.6190, "lon": 40.2905,
    },
    # ── 4. Elektrik & Trafo Riski ──────────────────────────────────────
    {
        "category": "Elektrik & Trafo Riski",
        "quality": "Kaliteli",
        "text": "Ana dağıtım trafosunda su teması nedeniyle patlamalar yaşanıyor; bölgedeki elektriğin derhal kesilmesi hayati önem taşıyor.",
        "lat": 40.6200, "lon": 40.2890,
    },
    {
        "category": "Elektrik & Trafo Riski",
        "quality": "Kaliteli",
        "text": "Devrilen elektrik direği ve kopan yüksek gerilim telleri suyun içine düştü, bölgede ciddi elektrik çarpılma riski var.",
        "lat": 40.6155, "lon": 40.2995,
    },
    {
        "category": "Elektrik & Trafo Riski",
        "quality": "Hatalı",
        "text": "trafo patlıo kımı buraa gelmesın elektırık carpıcak herkezı.",
        "lat": 40.6180, "lon": 40.2920,
    },
    {
        "category": "Elektrik & Trafo Riski",
        "quality": "Hatalı",
        "text": "teller koptu suya dustu heryer kıvılcım cok tehlıkelı gelmeyın.",
        "lat": 40.6200, "lon": 40.2890,
    },
    {
        "category": "Elektrik & Trafo Riski",
        "quality": "Hatalı",
        "text": "elektırıkler kesılmedı hala trifı yanıo baksın bırı patlıcak.",
        "lat": 40.6120, "lon": 40.3030,
    },
]

# ─────────────────────────────────────────────
# GEMINI CLIENT
# ─────────────────────────────────────────────
def init_gemini() -> genai.Client:
    client = genai.Client(api_key=API_KEY)
    return client


def extract_json(raw: str) -> Optional[dict]:
    """Modelin çıktısından JSON bloğunu temizleyip parse eder."""
    # Markdown kod bloğu varsa temizle
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Son çare: ilk { ... } bloğunu bul
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


def call_with_retry(fn, max_retries: int = 5):
    """
    Bir Gemini API çağrısını çalıştırır; 429 aldığında retryDelay kadar bekleyip tekrar dener.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                # retryDelay değerini hata metninden çıkar
                match = re.search(r"retryDelay.*?(\d+(?:\.\d+)?)", err_str)
                wait_sec = float(match.group(1)) if match else 60
                wait_sec = max(wait_sec, 1)  # en az 1 saniye
                print(f"     ⏳ Rate limit ({attempt}/{max_retries}) — {wait_sec:.0f}s bekleniyor...")
                time.sleep(wait_sec + 2)  # +2s emniyet payı
            else:
                raise  # başka hata ise yukari ilet
    raise RuntimeError(f"{max_retries} denemede de API çağrısı başarısız oldu.")


def analyze_scenario(client: genai.Client, scenario: dict, incident_id: str) -> dict:
    """Tek bir senaryoyu Gemini'ye gönderir; yazım düzeltme + analiz tek istekte yapılır."""
    original_text = scenario["text"]
    data_quality_tag = "high" if scenario["quality"] == "Kaliteli" else "low"

    user_message = (
        f"Aşağıdaki afet ihbarını analiz et ve Incident Report JSON şemasına uygun çıktı üret.\n\n"
        f"incident_id: {incident_id}\n"
        f"Kategori: {scenario['category']}\n"
        f"veri_kalitesi: {data_quality_tag}\n"
        f"Konum (lat, lon): {scenario['lat']}, {scenario['lon']}\n"
        f"Mesaj: {original_text}"
    )

    def _call():
        return client.models.generate_content(
            model=MODEL_NAME,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
            ),
        )
    response = call_with_retry(_call)
    raw_text = response.text.strip()

    parsed = extract_json(raw_text)
    if parsed is None:
        # Parse edilemezse ham metni wrapped olarak döndür
        parsed = {
            "incident_id": incident_id,
            "error": "JSON parse edilemedi",
            "raw_output": raw_text,
        }

    # corrected_text modelin JSON çıktısından alınır
    corrected_text = parsed.get("corrected_text") if parsed else None

    # Meta veri ekle
    parsed["_meta"] = {
        "category": scenario["category"],
        "quality": scenario["quality"],
        "original_text": original_text,
        "corrected_text": corrected_text,
        "coordinates": {"lat": scenario["lat"], "lon": scenario["lon"]},
    }
    return parsed


# ─────────────────────────────────────────────
# RAPORLAMA
# ─────────────────────────────────────────────
def print_summary(results: list[dict]) -> None:
    print("\n" + "═" * 70)
    print("  OASIS — ANALİZ ÖZET RAPORU")
    print("═" * 70)

    categories: dict[str, list] = {}
    for r in results:
        cat = r.get("_meta", {}).get("category", "Bilinmeyen")
        categories.setdefault(cat, []).append(r)

    for cat, items in categories.items():
        print(f"\n▶ {cat}")
        print("─" * 60)
        for item in items:
            meta = item.get("_meta", {})
            quality_icon = "✅" if meta.get("quality") == "Kaliteli" else "⚠️ "
            inc_id = item.get("incident_id", "?")
            action = item.get("recommended_action", item.get("error", "—"))
            severity = item.get("disaster", {}).get("severity_level", "?")
            urgency = item.get("disaster", {}).get("urgency", "?")
            confidence = item.get("confidence_score", "?")
            summary = item.get("summary", "")

            print(f"  {quality_icon} [{inc_id}] Aksiyon: {action}")
            print(f"     Şiddet: {severity}/5  |  Aciliyet: {urgency}  |  Güven: {confidence}")
            if meta.get("corrected_text"):
                print(f"     🔤 Orijinal : {meta.get('original_text')}")
                print(f"     ✏️  Düzeltme : {meta.get('corrected_text')}")
            if summary:
                print(f"     📋 Özet     : {summary}")

    print("\n" + "═" * 70)


# ─────────────────────────────────────────────
# DB SCHEMA MAPPING
# ─────────────────────────────────────────────
def to_geojson_point(lat: float, lon: float) -> dict:
    """PostGIS GEOMETRY(Point,4326) için GeoJSON Point formatı."""
    return {"type": "Point", "coordinates": [lon, lat]}


def build_raw_report(meta: dict, report_id: str) -> dict:
    """_meta dict → raw_reports tablosu satırı."""
    coords = meta.get("coordinates", {})
    lat, lon = coords.get("lat"), coords.get("lon")
    now = datetime.now(timezone.utc).isoformat()
    # Hatalı raporlarda düzeltilmiş metni kullan, Kaliteli raporlarda orijinali
    corrected = meta.get("corrected_text")
    event_text = corrected if corrected else meta.get("original_text")
    return {
        "id": str(uuid.uuid4()),
        "report_id": report_id,
        # Kaliteli → observer (gözlemci/yetkili), Hatalı → victim (etkilenen)
        "user_behavior": "observer" if meta.get("quality") == "Kaliteli" else "victim",
        "can_communicate": True,
        "gps_location": to_geojson_point(lat, lon) if lat and lon else None,
        "event_define": event_text,                  # düzeltilmiş metin
        "event_define_original": meta.get("original_text"),  # ham orijinal metin
        "event_capture": None,          # görsel veri yok
        "image_location": None,
        "image_date": None,
        "user_location": to_geojson_point(lat, lon) if lat and lon else None,
        "report_date": now,
    }


def build_processed_report(incident: dict, raw_id: str, report_id: str) -> dict:
    """Gemini analiz çıktısı → processed_reports tablosu satırı."""
    meta = incident.get("_meta", {})
    coords = meta.get("coordinates", {})
    lat = coords.get("lat")
    lon = coords.get("lon")
    now = datetime.now(timezone.utc).isoformat()

    disaster    = incident.get("disaster", {})
    human       = incident.get("human_impact", {})
    infra       = incident.get("infrastructure", {})
    confidence  = incident.get("confidence_score")
    data_qual   = incident.get("data_quality", "low")

    # severity_level 1-5 → severity_score 1-10 (doğrusal ölçekleme)
    sev_raw = disaster.get("severity_level")
    severity_score = round(sev_raw * 2.0, 1) if sev_raw is not None else None

    # vlm_analysis: Gemini'nin ürettiği tüm analiz alanlarını JSONB olarak sakla
    vlm_analysis = {
        "incident_id":          incident.get("incident_id"),
        "data_quality":         data_qual,
        "disaster":             disaster,
        "human_impact":         human,
        "infrastructure":       infra,
        "recommended_action":   incident.get("recommended_action"),
        "confidence_score":     confidence,
        "summary":              incident.get("summary"),
        "vr":                   incident.get("vr"),
    }

    # user_status: can_communicate + life_risk'ten türet
    life_risk = human.get("life_risk", False)
    trapped   = human.get("trapped_persons")
    if life_risk and trapped:
        user_status = "mahsur-hayati-risk"
    elif life_risk:
        user_status = "hayati-risk"
    elif trapped:
        user_status = "mahsur"
    else:
        user_status = "iletisimde"

    # location_match_score: data_quality + confidence birleşimi
    if data_qual == "high" and confidence is not None:
        location_match_score = round(min(confidence, 1.0), 2)
    elif confidence is not None:
        location_match_score = round(confidence * 0.6, 2)
    else:
        location_match_score = None

    return {
        "id":                   str(uuid.uuid4()),
        "report_id":            report_id,
        "raw_report_id":        raw_id,
        "user_behavior":        "observer" if meta.get("quality") == "Kaliteli" else "victim",
        "can_communicate":      True,
        "user_status":          user_status,
        "gps_location":         to_geojson_point(lat, lon) if lat and lon else None,
        "event_define":         meta.get("corrected_text") or meta.get("original_text"),
        "event_define_original": meta.get("original_text"),
        "disaster_type":        disaster.get("type"),
        "severity_score":       severity_score,
        "vlm_analysis":         vlm_analysis,
        "location_match_score": location_match_score,
        "user_location":        to_geojson_point(lat, lon) if lat and lon else None,
        "image_date":           None,
        "report_date":          now,
        "processed_at":         now,
    }


def save_results(results: list[dict]) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) Ham Gemini çıktısı (mevcut format)
    raw_output_path = os.path.join(OUTPUT_DIR, f"oasis_report_{timestamp}.json")
    with open(raw_output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 2) DB şemasına uygun çıktı
    raw_rows       = []
    processed_rows = []

    for incident in results:
        report_id = incident.get("incident_id", f"INC-{timestamp}")
        raw_row   = build_raw_report(incident.get("_meta", {}), report_id)
        proc_row  = build_processed_report(incident, raw_row["id"], report_id)
        raw_rows.append(raw_row)
        processed_rows.append(proc_row)

    db_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
        "raw_reports": raw_rows,
        "processed_reports": processed_rows,
    }

    db_output_path = os.path.join(OUTPUT_DIR, f"oasis_db_{timestamp}.json")
    with open(db_output_path, "w", encoding="utf-8") as f:
        json.dump(db_payload, f, ensure_ascii=False, indent=2)

    print(f"✅ Ham analiz kaydedildi  : {raw_output_path}")
    print(f"✅ DB şema çıktısı kaydedildi: {db_output_path}")
    return db_output_path


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("🚨 OASIS — Afet Karar Destek Sistemi Başlatılıyor...")
    print(f"   Model  : {MODEL_NAME}")
    print(f"   Toplam senaryo: {len(SCENARIOS)}\n")

    if API_KEY == "YOUR_API_KEY_HERE":
        print("❌ HATA: API anahtarı ayarlanmamış!")
        print("   Lütfen GEMINI_API_KEY ortam değişkenini tanımlayın:")
        print("   export GEMINI_API_KEY='your-key-here'")
        return

    client = init_gemini()
    results = []

    for i, scenario in enumerate(SCENARIOS, start=1):
        incident_id = f"INC-{i:03d}"
        quality_tag = "✅" if scenario["quality"] == "Kaliteli" else "⚠️ "
        print(f"  {quality_tag} [{incident_id}] {scenario['category']} ({scenario['quality']}) → işleniyor...")

        try:
            result = analyze_scenario(client, scenario, incident_id)
            results.append(result)
        except Exception as e:
            print(f"     ❌ Hata: {e}")
            results.append({
                "incident_id": incident_id,
                "error": str(e),
                "_meta": {
                    "category": scenario["category"],
                    "quality": scenario["quality"],
                    "original_text": scenario["text"],
                    "coordinates": {"lat": scenario["lat"], "lon": scenario["lon"]},
                },
            })

        # İstekler arası kısa礼儀 bekleme (rate limiti yumuşatır)
        time.sleep(1.5)

    print_summary(results)
    print()
    save_results(results)


if __name__ == "__main__":
    main()
