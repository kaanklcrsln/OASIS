"""
AI Servisi — Gösterge-Tabanlı (Indicator-Based) Afet Analizi
=============================================================
LLM: Gemma 3 27B, VLM: Gemini 2.5 Flash TTS.
LLM/VLM'den boolean göstergeler alınır, aciliyet puanı
deterministik formülle hesaplanır (LLM'e puan bırakılmaz).

Pipeline:
  1. LLM → metin analizi → boolean indicators
  2. VLM → görsel analizi → boolean indicators (fotoğraf varsa)
  3. indicator_scoring.calculate_severity() → severity_score (1–10)
  4. processed_reports tablosuna yaz

Önemli tasarım kararları:
  - LLM ve VLM AYRI rate limiter kullanır → VLM limiti dolsa bile LLM çalışır
  - VLM opsiyoneldir → VLM yoksa severity SADECE LLM'den hesaplanır
  - VLM sadece reliability_score'u etkiler (çapraz doğrulama)
  - Rate limiting: raporlar arası 2sn bekleme, quota hatası sonrası 15sn bekleme
"""
import os
import re
import json
import uuid
import base64
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Report, ProcessedReport, ExifFile
from app.config import settings
from app.services.indicator_scoring import (
    calculate_severity,
    determine_user_status,
    recommend_action,
    ALL_INDICATOR_KEYS,
)

logger = logging.getLogger(__name__)

def _env(name: str, default: str) -> str:
    """Boş bırakılmış ortam değişkenlerinde de varsayılanı kullan."""
    return os.getenv(name) or default


API_KEYS = [k for k in [settings.GEMINI_API_KEY] if k]
LLM_MODEL_NAME = _env("GEMMA_LLM_MODEL", "gemma-4-31b-it")
LLM_MODEL_FALLBACKS = [m.strip() for m in _env("GEMMA_LLM_FALLBACKS", "gemma-3-27b-it,gemma-3-12b-it").split(",") if m.strip()]
VLM_MODEL_NAME = _env("GEMINI_VLM_MODEL", "gemini-2.5-flash")
TEXT_ONLY_GEMMA_MODE = _env("TEXT_ONLY_GEMMA_MODE", "true").lower() == "true"
IMAGES_DIR = settings.IMAGES_DIR
LLM_PRE_CALL_DELAY_SECONDS = float(_env("LLM_PRE_CALL_DELAY_SECONDS", "4"))
LLM_RETRY_DELAY_SECONDS = float(_env("LLM_RETRY_DELAY_SECONDS", "45"))

# Rate limit flags
_vlm_disabled = False  # VLM quota dolunca True, LLM etkilenmez
_llm_quota_exhausted = False  # Tüm LLM modelleri quota dolunca True, retry atlanır

# ─────────────────────────────────────────────────────────
# INDICATOR KEYS — JSON şemasında kullanılacak göstergeler
# ─────────────────────────────────────────────────────────
_INDICATOR_SCHEMA = ",\n    ".join(
    [f'"{k}": boolean' for k in ALL_INDICATOR_KEYS]
)

# ─────────────────────────────────────────────
# SYSTEM PROMPT — Metin Analizi (LLM)
# ─────────────────────────────────────────────
SYSTEM_PROMPT_LLM = """Sen bir Afet Karar Destek Sistemi yapay zekâsısın.
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

# ─────────────────────────────────────────────
# SYSTEM PROMPT — Görsel Analiz (VLM)
# ─────────────────────────────────────────────
SYSTEM_PROMPT_VLM = """Sen bir Afet Görsel Analiz Sistemisin.
Fotoğrafı analiz et ve SADECE gözlemlerini boolean göstergelerle bildir.

SADECE FOTOĞRAFTA GÖREBİLDİKLERİNİ bildir.
Metin bilgisine bakma — sadece görselden çıkarım yap.

KESİN KURALLAR:
• Asla serbest metin yazma, SADECE JSON üret
• Fotoğrafta NET göremediğin şeyleri false yap
• Belirsizlikte false yap (güvenli taraf)

GÖRSEL İPUÇLARI:
• people_trapped: Enkaz altında/arasında insan görünüyor → true
• people_injured: Kan, yaralı insan, sedye → true
• life_threat: Tehlikeli ortamda insan (su içinde, yıkılmak üzere yapı yanında) → true
• children_elderly_at_risk: Çocuk/yaşlı/engelli görüntüsü → true
• large_crowd_affected: Kalabalık grup, tahliye → true
• building_collapsed: Yıkılmış/çökmüş yapı → true
• building_damaged: Çatlamış duvar, hasar görmüş bina → true
• road_blocked: Enkaz/su/toprak ile kapanmış yol → true
• utility_disrupted: Devrilmiş elektrik direği, kırık boru → true
• utility_dangerous: Kıvılcım, gaz kokusu emareleri, su+elektrik → true
• flood_water_rising: Yükselen su, su basmış alan → true
• fire_active: Alev, yoğun duman → true
• landslide_active: Kayan toprak/kaya → true
• hazmat_present: Kimyasal varil, renkli sıvı, uyarı işareti → true
• aftershock_risk: Çatlamış zemin, sallanma izleri → true
• no_communication: Yıkılmış baz istasyonu → true
• area_isolated: Kopmuş köprü, kapanmış tüm yollar → true
• rescue_requested: Yardım pankartı, el sallama → true

ÇIKTI DİLİ: Türkçe
SADECE JSON üret.

JSON ŞEMASI:
{
  "indicators": {
    """ + _INDICATOR_SCHEMA + """
  },
  "visual_description": "string (fotoğrafta ne görüldüğü, 1-2 cümle)",
  "image_quality": "clear | blurry | dark | partial"
}"""


def _extract_json(raw: str) -> Optional[dict]:
    """Model çıktısından JSON bloğunu parse et."""
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


def _build_user_message_llm(report: Report, lat: float, lon: float) -> str:
    """Report'tan LLM metin analizi için mesaj oluştur."""
    return (
        f"Aşağıdaki afet ihbarını analiz et ve boolean göstergelerle JSON çıktısı üret.\n\n"
        f"incident_id: {report.report_id}\n"
        f"Kullanıcı tipi: {report.user_behavior}\n"
        f"İletişim kurabilir mi: {report.can_communicate}\n"
        f"Konum (lat, lon): {lat}, {lon}\n"
        f"Mesaj: {report.event_define or 'Açıklama girilmedi'}"
    )


def _call_gemini(
    system_prompt: str,
    contents: list,
    call_type: str = "llm",
    model_name: Optional[str] = None,
) -> Optional[str]:
    """
    Gemini API'ye istek gönder; model + key çift failover destekli.

    call_type: "llm" veya "vlm"
    LLM için deneme sırası: önce LLM_MODEL_NAME (Gemma 4 31B),
    quota/rate hatası alınırsa LLM_MODEL_FALLBACKS'teki modeller devreye girer.
    Her model için tüm API key'ler denenir.
    VLM quota hatası alırsa global _vlm_disabled flag'i set edilir, LLM etkilenmez.
    """
    global _vlm_disabled, _llm_quota_exhausted

    if call_type == "vlm" and _vlm_disabled:
        logger.info("📷 VLM devre dışı (günlük limit aşıldı), atlanıyor")
        return None
    if call_type == "llm" and _llm_quota_exhausted:
        logger.warning("🚫 LLM quota tükendi (session flag), istek atlanıyor")
        return None
    if call_type == "vlm" and TEXT_ONLY_GEMMA_MODE:
        logger.info("📷 TEXT_ONLY_GEMMA_MODE aktif: VLM çağrısı atlandı")
        return None

    import time

    if call_type == "llm":
        primary = model_name or LLM_MODEL_NAME
        model_queue = [primary] + [m for m in LLM_MODEL_FALLBACKS if m != primary]
    else:
        model_queue = [model_name or VLM_MODEL_NAME]

    for model in model_queue:
        for i, api_key in enumerate(API_KEYS):
            try:
                client = genai.Client(api_key=api_key)
                if model.startswith("gemma"):
                    merged_contents = contents
                    if contents and isinstance(contents[0], str):
                        merged_contents = [
                            f"{system_prompt}\n\n{contents[0]}"
                        ] + contents[1:]
                    response = client.models.generate_content(
                        model=model,
                        contents=merged_contents,
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                        ),
                    )
                else:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.1,
                        ),
                    )
                raw_text = response.text.strip()
                logger.info(f"✅ {call_type.upper()} ({model}) API key #{i+1} ile başarılı")
                return raw_text
            except Exception as e:
                error_msg = str(e).lower()
                if "quota" in error_msg or "rate" in error_msg or "429" in error_msg or "resource_exhausted" in error_msg:
                    logger.warning(
                        f"⚠️ {call_type.upper()} ({model}) API key #{i+1} limit doldu "
                        f"[hata: {str(e)[:120]}], yedek deneniyor..."
                    )
                    time.sleep(5)
                    continue
                else:
                    logger.error(f"❌ {call_type.upper()} ({model}) beklenmeyen hata: {e}")
                    raise
        # Bu modelin tüm key'leri tükendi, bir sonraki modele geç
        if call_type == "llm" and model != model_queue[-1]:
            logger.warning(f"⚠️ LLM ({model}) tüm key'ler tükendi, fallback modele geçiliyor...")

    # Tüm model + key kombinasyonları tükendi
    if call_type == "vlm":
        _vlm_disabled = True
        logger.warning("🚫 VLM tüm key'ler tükendi — VLM devre dışı bırakıldı (sadece bu session)")
    else:
        _llm_quota_exhausted = True
        logger.warning("🚫 LLM tüm modeller ve key'ler tükendi — retry atlanacak (sadece bu session)")

    return None


async def analyze_report(report_id: uuid.UUID, db: AsyncSession) -> None:
    """
    BackgroundTask olarak çağrılır.
    Gösterge-Tabanlı (Indicator-Based) Analiz Pipeline:

    1. raw_reports'tan report'u al
    2. LLM analizi: metin → boolean indicators + disaster_type + summary
    3. VLM analizi: görsel → boolean indicators (fotoğraf varsa)
    4. indicator_scoring.calculate_severity() → deterministik puan
    5. indicator_scoring.determine_user_status() → user_status
    6. Sonucu processed_reports'a yaz

    Key limiti dolarsa otomatik yedek key'e geçer.
    """
    try:
        # 1. Report'u al
        result = await db.execute(select(Report).where(Report.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            logger.error(f"Report bulunamadı: {report_id}")
            return

        if not API_KEYS:
            logger.error("GEMINI_API_KEY tanımlı değil, AI analizi atlanıyor")
            return

        # GPS koordinatlarını çıkar
        lat, lon = 0.0, 0.0
        if report.gps_location is not None:
            try:
                from geoalchemy2.shape import to_shape
                point = to_shape(report.gps_location)
                lat, lon = point.y, point.x
            except Exception:
                pass

        # ═══════════════════════════════════════════════
        # ADIM 2: LLM Metin Analizi → boolean indicators
        # ═══════════════════════════════════════════════
        user_message = _build_user_message_llm(report, lat, lon)
        
        # Rate limit: LLM çağrısı öncesi bekleme
        await asyncio.sleep(LLM_PRE_CALL_DELAY_SECONDS)
        llm_raw = _call_gemini(
            SYSTEM_PROMPT_LLM,
            [user_message],
            call_type="llm",
            model_name=LLM_MODEL_NAME,
        )

        llm_parsed = None
        llm_indicators = {}
        disaster_type = None
        summary = None
        confidence = None
        data_qual = "low"

        if llm_raw:
            llm_parsed = _extract_json(llm_raw)
            if llm_parsed:
                llm_indicators = llm_parsed.get("indicators", {})
                disaster_type = llm_parsed.get("disaster_type")
                summary = llm_parsed.get("summary")
                confidence = llm_parsed.get("confidence_score")
                data_qual = llm_parsed.get("data_quality", "low")
                logger.info(f"📝 LLM analizi → {sum(1 for v in llm_indicators.values() if v)} aktif gösterge")
            else:
                logger.error(f"LLM JSON parse edilemedi: {llm_raw[:200]}")
        else:
            # LLM başarısız — quota bittiyse retry atla, değilse bekle ve dene
            if _llm_quota_exhausted:
                logger.warning("⏭️ LLM quota tükendi, retry atlanıyor — gereksiz API isteği engellendi")
            else:
                logger.warning(f"⏳ LLM yanıt alamadı, {LLM_RETRY_DELAY_SECONDS:.0f}sn sonra tekrar deneniyor...")
                await asyncio.sleep(LLM_RETRY_DELAY_SECONDS)
            llm_raw = None if _llm_quota_exhausted else _call_gemini(
                SYSTEM_PROMPT_LLM,
                [user_message],
                call_type="llm",
                model_name=LLM_MODEL_NAME,
            )
            if llm_raw:
                llm_parsed = _extract_json(llm_raw)
                if llm_parsed:
                    llm_indicators = llm_parsed.get("indicators", {})
                    disaster_type = llm_parsed.get("disaster_type")
                    summary = llm_parsed.get("summary")
                    confidence = llm_parsed.get("confidence_score")
                    data_qual = llm_parsed.get("data_quality", "low")
                    logger.info(f"📝 LLM analizi (retry) → {sum(1 for v in llm_indicators.values() if v)} aktif gösterge")
                else:
                    logger.error(f"LLM JSON parse edilemedi (retry): {llm_raw[:200]}")

            if not llm_parsed:
                logger.error(f"❌ LLM analizi tamamen başarısız: {report.report_id} — rapor kaydedilecek ama severity=1")
                # LLM tamamen başarısız → yine de DB'ye yaz (severity=1, sonra re-analyze ile düzelir)

        # ═══════════════════════════════════════════════
        # ADIM 3: VLM Görsel Analizi → boolean indicators
        # ═══════════════════════════════════════════════
        vlm_indicators = {}
        vlm_parsed = None
        has_valid_image = False
        has_vlm_result = False

        if report.image_path and TEXT_ONLY_GEMMA_MODE:
            vlm_parsed = {
                "status": "skipped",
                "reason": "text_only_gemma_mode",
                "note": "Görsel analizi devre dışı; rapor görselsiz (LLM-only) işlendi.",
            }
            logger.info("📷 TEXT_ONLY_GEMMA_MODE: görsel analizi atlandı")
        elif report.image_path:
            image_full_path = IMAGES_DIR / report.image_path
            if image_full_path.exists():
                try:
                    from PIL import Image as PILImage
                    with PILImage.open(image_full_path) as img:
                        img.verify()
                    with PILImage.open(image_full_path) as img:
                        w, h = img.size
                    if w < 50 or h < 50:
                        logger.warning(f"Görsel çok küçük ({w}x{h}), VLM atlanıyor")
                    else:
                        with open(image_full_path, "rb") as f:
                            image_bytes = f.read()
                        image_part = types.Part.from_bytes(
                            data=image_bytes,
                            mime_type="image/jpeg",
                        )
                        has_valid_image = True
                        logger.info(f"📷 VLM için görsel hazır: {w}x{h}px")

                        # VLM'e SADECE görsel gönder (metin yok)
                        vlm_message = "Bu fotoğrafı analiz et ve boolean göstergelerle JSON çıktısı üret."
                        
                        # Rate limit: VLM çağrısı öncesi bekleme (limit tükendiyse atla)
                        if not _vlm_disabled:
                            await asyncio.sleep(5)
                        vlm_raw = _call_gemini(
                            SYSTEM_PROMPT_VLM,
                            [image_part, vlm_message],
                            call_type="vlm",
                            model_name=VLM_MODEL_NAME,
                        )

                        if vlm_raw:
                            vlm_parsed = _extract_json(vlm_raw)
                            if vlm_parsed:
                                vlm_indicators = vlm_parsed.get("indicators", {})
                                has_vlm_result = True
                                logger.info(f"📷 VLM analizi → {sum(1 for v in vlm_indicators.values() if v)} aktif gösterge")
                            else:
                                logger.warning(f"VLM JSON parse edilemedi: {vlm_raw[:200]}")
                        else:
                            logger.warning("VLM API yanıtı alınamadı")
                            if _vlm_disabled:
                                vlm_parsed = {
                                    "status": "skipped",
                                    "reason": "vlm_token_or_quota_exhausted",
                                    "note": "Görsel analizi gerçekleştirilemedi; VLM limiti/tokenu durduğu için rapor görselsiz analiz edildi.",
                                }

                except Exception as e:
                    logger.warning(f"Görsel geçersiz, VLM atlanıyor: {e}")

        # Görsel var ama VLM analizi üretilemediyse görselsiz fallback notu bırak
        if has_valid_image and not has_vlm_result and vlm_parsed is None:
            vlm_parsed = {
                "status": "skipped",
                "reason": "visual_analysis_unavailable",
                "note": "Görsel analizi gerçekleştirilemedi; rapor görselsiz analiz edildi.",
            }

        # ═══════════════════════════════════════════════
        # ADIM 4: Deterministik Puanlama
        # ═══════════════════════════════════════════════
        # VLM sonucu yoksa, görsel gelse bile raporu görselsiz gibi işle
        scoring_result = calculate_severity(llm_indicators, vlm_indicators if has_vlm_result else None)
        severity_score = scoring_result["severity_score"]

        user_status = determine_user_status(
            llm_indicators,
            report.can_communicate,
            vlm_indicators if has_vlm_result else None,
        )

        action = recommend_action(
            llm_indicators,
            severity_score,
            vlm_indicators if has_vlm_result else None,
        )

        logger.info(
            f"🧮 Deterministik puan: {severity_score} | "
            f"Güvenilirlik: {scoring_result['reliability_score']} | "
            f"Kategoriler: {scoring_result['category_scores']} | "
            f"Aktif: {len(scoring_result['active_indicators'])} gösterge | "
            f"Çapraz doğrulama: {len(scoring_result['cross_validated'])} gösterge"
        )

        # location_match_score
        if data_qual == "high" and confidence is not None:
            location_match_score = round(min(confidence, 1.0), 2)
        elif confidence is not None:
            location_match_score = round(confidence * 0.6, 2)
        else:
            location_match_score = None

        now = datetime.now(timezone.utc)

        # ── user_location: EXIF konum vs GPS konum karşılaştırması ──
        exif_lat, exif_lon = None, None
        exif_result = await db.execute(
            select(ExifFile).where(ExifFile.report_id == report.id)
        )
        exif_record = exif_result.scalar_one_or_none()

        if exif_record and exif_record.latitude and exif_record.longitude:
            exif_lat = exif_record.latitude
            exif_lon = exif_record.longitude

        if exif_lat and exif_lon and lat and lon:
            import math
            R = 6371
            dlat = math.radians(exif_lat - lat)
            dlon = math.radians(exif_lon - lon)
            a = (math.sin(dlat / 2) ** 2 +
                 math.cos(math.radians(lat)) * math.cos(math.radians(exif_lat)) *
                 math.sin(dlon / 2) ** 2)
            distance_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            distance_m = distance_km * 1000  # metre cinsine çevir

            if distance_m < 27:
                # < 27m: EXIF ve GPS neredeyse aynı nokta → EXIF konumu güvenilir
                final_lat, final_lon = exif_lat, exif_lon
                logger.info(f"user_location = EXIF konum (mesafe: {distance_m:.1f}m, < 27m)")
            elif distance_m < 52:
                # 27-52m: Makul fark → ortalama al
                final_lat = (lat + exif_lat) / 2
                final_lon = (lon + exif_lon) / 2
                logger.info(f"user_location = GPS+EXIF ortalaması (mesafe: {distance_m:.1f}m, 27-52m)")
            else:
                # 52m+: EXIF konum çok uzak → GPS konumunu kullan
                final_lat, final_lon = lat, lon
                logger.info(f"user_location = GPS konum (EXIF çok uzak: {distance_m:.1f}m, > 52m)")
        elif lat and lon:
            final_lat, final_lon = lat, lon
            logger.info("user_location = GPS konum (EXIF konum yok)")
        else:
            final_lat, final_lon = 0.0, 0.0

        user_location_wkt = (
            f"SRID=4326;POINT({final_lon} {final_lat})"
            if final_lat and final_lon else None
        )

        # ═══════════════════════════════════════════════
        # ADIM 5: Birleşik analiz JSON'u hazırla (llm_analysis sütunu)
        # ═══════════════════════════════════════════════
        combined_analysis = {
            "llm": llm_parsed,
            "vlm": vlm_parsed,
            "scoring": scoring_result,
            "recommended_action": action,
            "summary": summary,
        }

        # indicators_json: saf gösterge verileri (ayrı sütun)
        indicators_data = {
            "llm_indicators": llm_indicators,
            "vlm_indicators": vlm_indicators if has_vlm_result else None,
            "active_indicators": scoring_result["active_indicators"],
            "cross_validated": scoring_result["cross_validated"],
            "category_scores": scoring_result["category_scores"],
            "reliability_score": scoring_result["reliability_score"],
            "formula_breakdown": scoring_result["formula_breakdown"],
        }

        # ═══════════════════════════════════════════════
        # ADIM 6: processed_reports'a yaz
        # ═══════════════════════════════════════════════
        existing_result = await db.execute(
            select(ProcessedReport).where(ProcessedReport.raw_report_id == report.id)
        )
        existing = existing_result.scalar_one_or_none()

        processed_payload = {
            "user_behavior": report.user_behavior,
            "can_communicate": report.can_communicate,
            "user_status": user_status,
            "gps_location": f"SRID=4326;POINT({lon} {lat})" if lat and lon else None,
            "disaster_type": disaster_type,
            "severity_score": severity_score,
            "vlm_analysis": vlm_parsed,
            "llm_analysis": combined_analysis,
            "indicators_json": indicators_data,
            "location_match_score": location_match_score,
            "user_location": user_location_wkt,
            "image_date": report.image_date,
            "report_date": report.report_date,
            "processed_at": now,
        }

        if existing:
            for field, value in processed_payload.items():
                setattr(existing, field, value)
            logger.info(f"🔄 Re-analysis (gösterge-tabanlı): {report.report_id}")
        else:
            processed = ProcessedReport(
                report_id=report.report_id,
                raw_report_id=report.id,
                **processed_payload,
            )
            db.add(processed)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.warning(f"⚠️ Yarış durumu algılandı, update fallback uygulanıyor: {report.report_id}")
            conflict_result = await db.execute(
                select(ProcessedReport).where(ProcessedReport.report_id == report.report_id)
            )
            conflict_row = conflict_result.scalar_one_or_none()
            if not conflict_row:
                raise

            conflict_row.raw_report_id = report.id
            for field, value in processed_payload.items():
                setattr(conflict_row, field, value)
            await db.commit()

        logger.info(
            f"✅ Gösterge-tabanlı analiz tamamlandı: {report.report_id} → "
            f"disaster={disaster_type} severity={severity_score} "
            f"status={user_status} action={action} "
            f"indicators={len(scoring_result['active_indicators'])}"
        )

    except Exception as e:
        logger.error(f"❌ AI analiz hatası ({report_id}): {e}")
        await db.rollback()
