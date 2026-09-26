"""
Gösterge-Tabanlı (Indicator-Based) Deterministik Aciliyet Puanlama Modülü
=========================================================================

LLM/VLM'den gelen boolean göstergeler üzerinden deterministik ağırlıklı
formülle severity_score (1–10) hesaplar.

Kategori Ağırlıkları:
  - İnsan Faktörü      : maks 4.0 puan  (en yüksek öncelik)
  - Altyapı Hasarı      : maks 3.0 puan
  - Çevresel Tehdit     : maks 2.0 puan
  - İletişim/Erişim     : maks 1.0 puan
  ─────────────────────────────────
  TOPLAM                : maks 10.0 puan

Güvenilirlik (reliability_score):
  Çapraz doğrulama (LLM+VLM aynı gösterge) puana bonus VERMEZ.
  Bunun yerine ayrı bir reliability_score (0.3–1.0) üretir:
    reliability = 0.5 + 0.5 × (cross_validated_count / active_count)
  Bu skor "veriye ne kadar güvenilir" sorusunu yanıtlar.
"""

from typing import Optional

# ─────────────────────────────────────────────
# İNDİKATÖR TANIMLARI VE AĞIRLIKLARI
# ─────────────────────────────────────────────

# Her indikatör: (kategori, baz_puan)
# Aynı kategoride birden fazla gösterge true olabilir, ama
# kategori toplamı asla maks değeri aşamaz (capped).

INDICATOR_WEIGHTS = {
    # ── İNSAN FAKTÖRÜ (maks 4.0) ──
    "people_trapped":           ("human", 2.5),   # mahsur kişi var
    "people_injured":           ("human", 2.0),   # yaralı var
    "life_threat":              ("human", 2.0),   # hayati tehlike
    "children_elderly_at_risk": ("human", 1.5),   # çocuk/yaşlı risk altında
    "large_crowd_affected":     ("human", 1.0),   # çok sayıda kişi etkilendi

    # ── ALTYAPI HASARI (maks 3.0) ──
    "building_collapsed":       ("infra", 2.0),   # bina çökmüş/ağır hasarlı
    "building_damaged":         ("infra", 1.0),   # bina orta düzey hasar
    "road_blocked":             ("infra", 1.5),   # yol kapanmış/ulaşım kesilmiş
    "utility_disrupted":        ("infra", 1.0),   # elektrik/su/gaz kesilmiş
    "utility_dangerous":        ("infra", 1.5),   # elektrik/gaz tehlikeli sızıntı

    # ── ÇEVRESEL TEHDİT (maks 2.0) ──
    "flood_water_rising":       ("enviro", 1.5),  # su seviyesi yükseliyor
    "fire_active":              ("enviro", 1.5),  # aktif yangın
    "landslide_active":         ("enviro", 1.5),  # aktif heyelan
    "hazmat_present":           ("enviro", 1.0),  # tehlikeli madde
    "aftershock_risk":          ("enviro", 1.0),  # artçı deprem riski

    # ── İLETİŞİM / ERİŞİM (maks 1.0) ──
    "no_communication":         ("comms", 0.8),   # iletişim kesilmiş
    "area_isolated":            ("comms", 0.7),   # bölge izole / ulaşılamıyor
    "rescue_requested":         ("comms", 0.5),   # kurtarma talebi açık
}

CATEGORY_CAPS = {
    "human":  4.0,
    "infra":  3.0,
    "enviro": 2.0,
    "comms":  1.0,
}

# Çapraz doğrulama: puana etki ETMEZ, ayrı reliability_score üretir

# ─────────────────────────────────────────────
# ANA PUANLAMA FONKSİYONU
# ─────────────────────────────────────────────

def calculate_severity(
    llm_indicators: dict,
    vlm_indicators: Optional[dict] = None,
) -> dict:
    """
    Boolean göstergelerden deterministik severity_score (1.0 – 10.0) hesaplar.
    Çapraz doğrulama puana etki ETMEZ — ayrı reliability_score üretir.

    Args:
        llm_indicators: LLM'den gelen boolean dict  {"people_trapped": true, ...}
        vlm_indicators: VLM'den gelen boolean dict (görsel analiz, opsiyonel)

    Returns:
        {
            "severity_score": float,         # 1.0 – 10.0
            "reliability_score": float,      # 0.3 – 1.0 (güvenilirlik)
            "category_scores": {             # her kategori kendi puanı
                "human": float,
                "infra": float,
                "enviro": float,
                "comms": float,
            },
            "active_indicators": [str],      # true olan göstergeler
            "cross_validated": [str],        # LLM+VLM eşleşen göstergeler
            "formula_breakdown": str,        # insana okunur açıklama
        }
    """
    if not llm_indicators:
        llm_indicators = {}
    if vlm_indicators is None:
        vlm_indicators = {}

    category_raw = {"human": 0.0, "infra": 0.0, "enviro": 0.0, "comms": 0.0}
    active_indicators = []
    cross_validated = []

    for indicator_name, (category, base_weight) in INDICATOR_WEIGHTS.items():
        llm_val = bool(llm_indicators.get(indicator_name, False))
        vlm_val = bool(vlm_indicators.get(indicator_name, False))

        # En az birisi true ise gösterge aktif
        if llm_val or vlm_val:
            # Puana düz ağırlık — bonus YOK
            category_raw[category] += base_weight
            active_indicators.append(indicator_name)

            # Çapraz doğrulama: ikisi de true → sadece işaretle
            if llm_val and vlm_val:
                cross_validated.append(indicator_name)

    # Kategori tavanlama (cap)
    category_capped = {}
    for cat, raw_val in category_raw.items():
        cap = CATEGORY_CAPS[cat]
        category_capped[cat] = round(min(raw_val, cap), 2)

    # Toplam ham puan
    total_raw = sum(category_capped.values())

    # Min 1.0 (hiç gösterge yoksa bile minimum rapor edildi)
    severity_score = round(max(total_raw, 1.0), 1)
    # Maks 10.0 (kap toplamı zaten 10.0 ama güvenlik için)
    severity_score = min(severity_score, 10.0)

    # ── Güvenilirlik skoru (reliability_score) ──
    # Çapraz doğrulama puanı ARTIRMAZ, güvenilirliği artırır.
    # Formül: 0.5 + 0.5 × (cross_count / active_count)
    #   0 cross    → 0.50 (tek kaynak)
    #   yarısı     → 0.75
    #   hepsi      → 1.00 (yüksek güven)
    #   gösterge 0 → 0.30 (veri yok, düşük güven)
    total_active = len(active_indicators)
    total_cross = len(cross_validated)
    if total_active > 0:
        reliability_score = round(0.5 + 0.5 * (total_cross / total_active), 2)
    else:
        reliability_score = 0.30

    # Okunabilir formül açıklaması
    parts = []
    for cat, label in [("human", "İnsan"), ("infra", "Altyapı"), ("enviro", "Çevre"), ("comms", "İletişim")]:
        if category_capped[cat] > 0:
            parts.append(f"{label}:{category_capped[cat]}")
    breakdown = " + ".join(parts) if parts else "Gösterge yok → taban puan"
    breakdown += f" = {severity_score}"

    return {
        "severity_score": severity_score,
        "reliability_score": reliability_score,
        "category_scores": category_capped,
        "active_indicators": active_indicators,
        "cross_validated": cross_validated,
        "formula_breakdown": breakdown,
    }


# ─────────────────────────────────────────────
# KULLANICI DURUM TÜRETMESİ
# ─────────────────────────────────────────────

def determine_user_status(
    llm_indicators: dict,
    can_communicate: bool,
    vlm_indicators: Optional[dict] = None,
) -> str:
    """
    Boolean göstergelerden user_status türet.

    Kural sırası (ilk eşleşen kazanır):
      1. people_trapped + life_threat → "mahsur-hayati-risk"
      2. life_threat → "hayati-risk"
      3. people_trapped → "mahsur"
      4. can_communicate=False + no_communication → "hayati-risk"
      5. can_communicate=True → "iletisimde"
      6. Hiçbiri → "bilinmiyor"
    """
    if not llm_indicators:
        llm_indicators = {}
    if vlm_indicators is None:
        vlm_indicators = {}

    # Merge: herhangi birinden true geldiyse true
    def _any(key):
        return bool(llm_indicators.get(key, False)) or bool(vlm_indicators.get(key, False))

    trapped = _any("people_trapped")
    life_threat = _any("life_threat")
    no_comm = _any("no_communication")

    if trapped and life_threat:
        return "mahsur-hayati-risk"
    if life_threat:
        return "hayati-risk"
    if trapped:
        return "mahsur"
    if not can_communicate and no_comm:
        return "hayati-risk"
    if can_communicate:
        return "iletisimde"
    return "bilinmiyor"


# ─────────────────────────────────────────────
# ACIL AKSİYON ÖNERİSİ
# ─────────────────────────────────────────────

def recommend_action(
    llm_indicators: dict,
    severity_score: float,
    vlm_indicators: Optional[dict] = None,
) -> str:
    """
    Göstergeler + severity_score'a göre aksiyon önerisi.

    Kural sırası:
      1. people_trapped VEYA building_collapsed → "dispatch_rescue"
      2. people_injured → "dispatch_medical"
      3. severity >= 7.0 → "dispatch_rescue"
      4. severity >= 5.0 → "needs_review"
      5. severity >= 3.0 → "monitor"
      6. → "monitor"
    """
    if not llm_indicators:
        llm_indicators = {}
    if vlm_indicators is None:
        vlm_indicators = {}

    def _any(key):
        return bool(llm_indicators.get(key, False)) or bool(vlm_indicators.get(key, False))

    if _any("people_trapped") or _any("building_collapsed"):
        return "dispatch_rescue"
    if _any("people_injured"):
        return "dispatch_medical"
    if severity_score >= 7.0:
        return "dispatch_rescue"
    if severity_score >= 5.0:
        return "needs_review"
    return "monitor"


# ─────────────────────────────────────────────
# TÜM İNDİKATÖR ANAHTARLARI (şema olarak)
# ─────────────────────────────────────────────

ALL_INDICATOR_KEYS = list(INDICATOR_WEIGHTS.keys())
"""Gemini promptuna gönderilecek tüm indikatör anahtarlarının listesi."""
