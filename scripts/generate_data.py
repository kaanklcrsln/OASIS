#!/usr/bin/env python3
"""
OASIS — 100 rapor üret, işle, DB'ye yaz ve test_reports_processed.json kaydet.
Gemma/AI kullanılmaz — indicator_scoring mantığı insan tarafından simüle edilir.

Kullanım: python3 generate_data.py
"""

import json
import math
import uuid
import subprocess
import sys
import os
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DB_CONTAINER = "oasis_db"
DB_USER = "oasis_user"
DB_NAME = "oasis"

# ── Indicator scoring (indicator_scoring.py'deki mantık) ──────────────────────

INDICATOR_WEIGHTS = {
    "people_trapped":           ("human",  2.5),
    "people_injured":           ("human",  2.0),
    "life_threat":              ("human",  2.0),
    "children_elderly_at_risk": ("human",  1.5),
    "large_crowd_affected":     ("human",  1.0),
    "building_collapsed":       ("infra",  2.0),
    "building_damaged":         ("infra",  1.0),
    "road_blocked":             ("infra",  1.5),
    "utility_disrupted":        ("infra",  1.0),
    "utility_dangerous":        ("infra",  1.5),
    "flood_water_rising":       ("enviro", 1.5),
    "fire_active":              ("enviro", 1.5),
    "landslide_active":         ("enviro", 1.5),
    "hazmat_present":           ("enviro", 1.0),
    "aftershock_risk":          ("enviro", 1.0),
    "no_communication":         ("comms",  0.8),
    "area_isolated":            ("comms",  0.7),
    "rescue_requested":         ("comms",  0.5),
}

CATEGORY_CAPS = {"human": 4.0, "infra": 3.0, "enviro": 2.0, "comms": 1.0}


def calculate_severity(llm_indicators: dict) -> dict:
    category_raw = {"human": 0.0, "infra": 0.0, "enviro": 0.0, "comms": 0.0}
    active = []
    for key, (cat, w) in INDICATOR_WEIGHTS.items():
        if llm_indicators.get(key, False):
            category_raw[cat] += w
            active.append(key)
    capped = {cat: round(min(v, CATEGORY_CAPS[cat]), 2) for cat, v in category_raw.items()}
    total = sum(capped.values())
    score = round(min(max(total, 1.0), 10.0), 1)
    reliability = 0.50 if active else 0.30
    parts = []
    for cat, label in [("human","İnsan"),("infra","Altyapı"),("enviro","Çevre"),("comms","İletişim")]:
        if capped[cat] > 0:
            parts.append(f"{label}:{capped[cat]}")
    breakdown = (" + ".join(parts) if parts else "Gösterge yok → taban puan") + f" = {score}"
    return {
        "severity_score": score,
        "reliability_score": reliability,
        "category_scores": capped,
        "active_indicators": active,
        "cross_validated": [],
        "formula_breakdown": breakdown,
    }


def determine_user_status(llm: dict, can_communicate: bool) -> str:
    trapped = llm.get("people_trapped", False)
    life = llm.get("life_threat", False)
    no_comm = llm.get("no_communication", False)
    if trapped and life:
        return "mahsur-hayati-risk"
    if life:
        return "hayati-risk"
    if trapped:
        return "mahsur"
    if not can_communicate and no_comm:
        return "hayati-risk"
    if can_communicate:
        return "iletisimde"
    return "bilinmiyor"


def recommend_action(llm: dict, score: float) -> str:
    if llm.get("people_trapped") or llm.get("building_collapsed"):
        return "dispatch_rescue"
    if llm.get("people_injured"):
        return "dispatch_medical"
    if score >= 7.0:
        return "dispatch_rescue"
    if score >= 5.0:
        return "needs_review"
    return "monitor"


# ── 50 yeni rapor tanımları ───────────────────────────────────────────────────

BASE_TIME = datetime(2026, 2, 27, 10, 0, 0, tzinfo=timezone(timedelta(hours=3)))


def ts(minutes_offset: int) -> str:
    return (BASE_TIME + timedelta(minutes=minutes_offset)).isoformat()


NEW_REPORTS = [
    # Cluster 1: Heyelan bölgesi — kuzey
    {
        "id": "aky-051", "report_id": "AKY-RPT-051",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9082, 40.9835]},
        "event_define": "Kuzey yamaçta büyük bir toprak kütlesi kaymaya devam ediyor, evlere yaklaşıyor.",
        "event_capture": "Heyelan devam ediyor.",
        "report_date": ts(0), "created_at": ts(0),
    },
    {
        "id": "aky-052", "report_id": "AKY-RPT-052",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9079, 40.9838]},
        "event_define": "Heyelan evimizin bahçesine kadar geldi, bodrum katımızı toprak doldurdu, mahsur kaldık.",
        "event_capture": "Ev zarar gördü.",
        "report_date": ts(2), "created_at": ts(2),
    },
    {
        "id": "aky-053", "report_id": "AKY-RPT-053",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9085, 40.9833]},
        "event_define": "toprak kaydı üstümüze çatı cöktü çıkamıyoruz kurtarın.",
        "event_capture": "Enkaz altında.",
        "report_date": ts(4), "created_at": ts(4),
    },
    {
        "id": "aky-054", "report_id": "AKY-RPT-054",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9077, 40.9841]},
        "event_define": "Yamaç tamamen çökmüş durumda, 3 ev toprak altında kaldı, içeride sesler duyuluyor.",
        "event_capture": "Çoklu bina hasarı.",
        "report_date": ts(6), "created_at": ts(6),
    },
    # Cluster 2: Köprü ve yol hasarı — batı
    {
        "id": "aky-055", "report_id": "AKY-RPT-055",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9045, 40.9798]},
        "event_define": "Batıdaki ahşap köprü sel sularına dayanamayıp çöktü, bölge yolu tamamen kapandı.",
        "event_capture": "Köprü çöktü.",
        "report_date": ts(8), "created_at": ts(8),
    },
    {
        "id": "aky-056", "report_id": "AKY-RPT-056",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9048, 40.9795]},
        "event_define": "Köprü yıkıldı, karşı yakada mahsur kaldık, 15 kişi burada bekliyoruz.",
        "event_capture": "Toplu mahsur.",
        "report_date": ts(10), "created_at": ts(10),
    },
    {
        "id": "aky-057", "report_id": "AKY-RPT-057",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9051, 40.9801]},
        "event_define": "Yol tamamen çökmüş, araç geçişi imkansız, sadece yaya geçişi mümkün ama o da tehlikeli.",
        "event_capture": "Yol kullanılamaz.",
        "report_date": ts(12), "created_at": ts(12),
    },
    # Cluster 3: Yangın — güneydoğu
    {
        "id": "aky-058", "report_id": "AKY-RPT-058",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9198, 40.9762]},
        "event_define": "Güneydoğu mahallede bir depoda yangın başladı, dumanlar yükseliyor, itfaiye giremez yollar kapalı.",
        "event_capture": "Yangın aktif.",
        "report_date": ts(14), "created_at": ts(14),
    },
    {
        "id": "aky-059", "report_id": "AKY-RPT-059",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9202, 40.9758]},
        "event_define": "Yangın komşu binalara sıçradı, içeride yaşlı bir adam çıkamıyor yardım edin.",
        "event_capture": "Yangın yayılıyor.",
        "report_date": ts(16), "created_at": ts(16),
    },
    {
        "id": "aky-060", "report_id": "AKY-RPT-060",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9195, 40.9765]},
        "event_define": "Yangın büyüdü, gaz tüplerinin patlaması beklenebilir, bölgeden uzaklaşın.",
        "event_capture": "Patlama riski.",
        "report_date": ts(18), "created_at": ts(18),
    },
    {
        "id": "aky-061", "report_id": "AKY-RPT-061",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9199, 40.9760]},
        "event_define": "yangın var dumanı yutuyoruz pencere açamıyoruz kurtarın bizi.",
        "event_capture": "Duman zehirlenmesi riski.",
        "report_date": ts(20), "created_at": ts(20),
    },
    # Cluster 4: Sel baskını — orta-kuzey
    {
        "id": "aky-062", "report_id": "AKY-RPT-062",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9135, 40.9845]},
        "event_define": "Dere taştı, mahalle tamamen su altında, evlerin zemin katları kullanılamaz durumda.",
        "event_capture": "Geniş taşkın.",
        "report_date": ts(22), "created_at": ts(22),
    },
    {
        "id": "aky-063", "report_id": "AKY-RPT-063",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9138, 40.9848]},
        "event_define": "Evimiz su aldı, çocuklarımla üst kata çıktık ama su yükselmeye devam ediyor.",
        "event_capture": "Aile mahsur.",
        "report_date": ts(24), "created_at": ts(24),
    },
    {
        "id": "aky-064", "report_id": "AKY-RPT-064",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9132, 40.9851]},
        "event_define": "su cikti tavan kati bitti telefon bitmek uzere yardm.",
        "event_capture": "Kritik durum.",
        "report_date": ts(26), "created_at": ts(26),
    },
    {
        "id": "aky-065", "report_id": "AKY-RPT-065",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9141, 40.9842]},
        "event_define": "Taşkın genişledi, araçlar sürüklenmeye başladı, akıntı çok kuvvetli.",
        "event_capture": "Araçlar sürükleniyor.",
        "report_date": ts(28), "created_at": ts(28),
    },
    # Cluster 5: Elektrik altyapısı — doğu
    {
        "id": "aky-066", "report_id": "AKY-RPT-066",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9225, 40.9815]},
        "event_define": "Doğu mahallede tüm elektrikler kesildi, hastanedeki jeneratör de çalışmıyor.",
        "event_capture": "Elektrik yok.",
        "report_date": ts(30), "created_at": ts(30),
    },
    {
        "id": "aky-067", "report_id": "AKY-RPT-067",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9228, 40.9812]},
        "event_define": "Elektrik yok, evde yoğun bakım cihazı olan hasta var, pil 2 saat dayanır.",
        "event_capture": "Tıbbi cihaz krizi.",
        "report_date": ts(32), "created_at": ts(32),
    },
    {
        "id": "aky-068", "report_id": "AKY-RPT-068",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9222, 40.9818]},
        "event_define": "Yüksek gerilim hattı koptu su birikintisine düştü, bölgeye girmeyin.",
        "event_capture": "Elektrik tehlikesi.",
        "report_date": ts(34), "created_at": ts(34),
    },
    # Cluster 6: Tıbbi aciller — merkez
    {
        "id": "aky-069", "report_id": "AKY-RPT-069",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9148, 40.9808]},
        "event_define": "Kalp krizi geçiren komşu var, ambulans yolları kapalı olduğu için gelemiyor.",
        "event_capture": "Kalp krizi.",
        "report_date": ts(36), "created_at": ts(36),
    },
    {
        "id": "aky-070", "report_id": "AKY-RPT-070",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9152, 40.9812]},
        "event_define": "Diyabetik şoku olan çocuğum var, insülin lazım ama eczaneye ulaşamıyoruz.",
        "event_capture": "Tıbbi ilaç acili.",
        "report_date": ts(38), "created_at": ts(38),
    },
    {
        "id": "aky-071", "report_id": "AKY-RPT-071",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9145, 40.9805]},
        "event_define": "Mahallede 3 yaralı var, biri ağır, sedye gerekiyor ama yollar kapalı.",
        "event_capture": "Çoklu yaralı.",
        "report_date": ts(40), "created_at": ts(40),
    },
    # Cluster 7: Altyapı hasarı — güney
    {
        "id": "aky-072", "report_id": "AKY-RPT-072",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9118, 40.9758]},
        "event_define": "Ana su borusu patlamış, yoldan büyük bir su fışkırıyor, yol çöküyor.",
        "event_capture": "Boru hattı hasarı.",
        "report_date": ts(42), "created_at": ts(42),
    },
    {
        "id": "aky-073", "report_id": "AKY-RPT-073",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9115, 40.9762]},
        "event_define": "Doğalgaz borusu kırık, koku yayılıyor, bölgede kıvılcım çıkarsa patlama olur.",
        "event_capture": "Gaz sızıntısı.",
        "report_date": ts(44), "created_at": ts(44),
    },
    {
        "id": "aky-074", "report_id": "AKY-RPT-074",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9122, 40.9755]},
        "event_define": "Gaz kokusu çok yoğun, evlerden çıkamıyoruz ama içeride de durmak tehlikeli.",
        "event_capture": "Tahliye gerekli.",
        "report_date": ts(46), "created_at": ts(46),
    },
    # Cluster 8: Okul ve kamu binaları
    {
        "id": "aky-075", "report_id": "AKY-RPT-075",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9102, 40.9792]},
        "event_define": "İlkokul bodrum katı su doldu, okulda mahsur 35 çocuk ve 3 öğretmen var.",
        "event_capture": "Okul mahsur.",
        "report_date": ts(48), "created_at": ts(48),
    },
    {
        "id": "aky-076", "report_id": "AKY-RPT-076",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9105, 40.9795]},
        "event_define": "Sağlık ocağı zemini su bastı, içerideki hastalar üst kata taşındı ama acil müdahale gerekiyor.",
        "event_capture": "Sağlık tesisi etkilendi.",
        "report_date": ts(50), "created_at": ts(50),
    },
    {
        "id": "aky-077", "report_id": "AKY-RPT-077",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9099, 40.9789]},
        "event_define": "Muhtarlık binası çöktü içeridekileri kurtarın.",
        "event_capture": "Bina çöktü.",
        "report_date": ts(52), "created_at": ts(52),
    },
    # Düşük şiddetli gözlemler (noise / singleton beklenenler)
    {
        "id": "aky-078", "report_id": "AKY-RPT-078",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9055, 40.9840]},
        "event_define": "Yoldaki çukurlar derinleşti, araçlar geçerken zorlanıyor.",
        "event_capture": "Yol hasarı.",
        "report_date": ts(54), "created_at": ts(54),
    },
    {
        "id": "aky-079", "report_id": "AKY-RPT-079",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9062, 40.9822]},
        "event_define": "Park alanı su altında, araçlar içinde mahsur kaldı.",
        "event_capture": "Park alanı su altında.",
        "report_date": ts(56), "created_at": ts(56),
    },
    {
        "id": "aky-080", "report_id": "AKY-RPT-080",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9178, 40.9842]},
        "event_define": "Zemin katta bodrum katımız su dolu, eşyalarımız mahvoldu, yukarıdayız ama yiyecek azalıyor.",
        "event_capture": "Ev su aldı.",
        "report_date": ts(58), "created_at": ts(58),
    },
    {
        "id": "aky-081", "report_id": "AKY-RPT-081",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9158, 40.9769]},
        "event_define": "İstinat duvarı çatlamış, yıkılma tehlikesi var alt sokak boşaltılmalı.",
        "event_capture": "Duvar tehlikesi.",
        "report_date": ts(60), "created_at": ts(60),
    },
    # Cluster 9: Nehir kıyısı taşkın
    {
        "id": "aky-082", "report_id": "AKY-RPT-082",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9068, 40.9775]},
        "event_define": "Nehir seviyesi kritik düzeyi aştı, sahil şeridindeki evler tehlike altında.",
        "event_capture": "Nehir taşıyor.",
        "report_date": ts(62), "created_at": ts(62),
    },
    {
        "id": "aky-083", "report_id": "AKY-RPT-083",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9072, 40.9778]},
        "event_define": "Nehir kenarındaki evimiz su aldı, yaşlı annem hareket edemez acil bot lazım.",
        "event_capture": "Hareket edemez yaşlı.",
        "report_date": ts(64), "created_at": ts(64),
    },
    {
        "id": "aky-084", "report_id": "AKY-RPT-084",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9065, 40.9772]},
        "event_define": "nehir girdi içeri cıkamıyoruzz su bel seviyesinde yardm.",
        "event_capture": "Su içeri girdi.",
        "report_date": ts(66), "created_at": ts(66),
    },
    {
        "id": "aky-085", "report_id": "AKY-RPT-085",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9075, 40.9781]},
        "event_define": "Nehir kıyısındaki 6 ev sular altında, sakinler çatılara çıkmış bot bekliyor.",
        "event_capture": "Çatıda bekleyen sakinler.",
        "report_date": ts(68), "created_at": ts(68),
    },
    # Cluster 10: Kuzey-doğu sel
    {
        "id": "aky-086", "report_id": "AKY-RPT-086",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9242, 40.9855]},
        "event_define": "Kuzeydoğu mahalleden gelen sel suları tüm çevreyi kapladı, araç trafiği durdu.",
        "event_capture": "Sel yayılıyor.",
        "report_date": ts(70), "created_at": ts(70),
    },
    {
        "id": "aky-087", "report_id": "AKY-RPT-087",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9245, 40.9858]},
        "event_define": "Sel suları dükkanımı bastı, kasada mahsur kaldım, kapı açılmıyor.",
        "event_capture": "Mahsur ticari alan.",
        "report_date": ts(72), "created_at": ts(72),
    },
    {
        "id": "aky-088", "report_id": "AKY-RPT-088",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9239, 40.9852]},
        "event_define": "Tünel girişi sel suları nedeniyle kapandı, araçlar mahsur kaldı içeride.",
        "event_capture": "Tünel kapandı.",
        "report_date": ts(74), "created_at": ts(74),
    },
    # Düşük öncelikli / gürültü raporları
    {
        "id": "aky-089", "report_id": "AKY-RPT-089",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9188, 40.9778]},
        "event_define": "Sokakta büyük bir çukur oluştu, yavaş yürüyenler için tehlikeli.",
        "event_capture": "Çukur oluştu.",
        "report_date": ts(76), "created_at": ts(76),
    },
    {
        "id": "aky-090", "report_id": "AKY-RPT-090",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9098, 40.9808]},
        "event_define": "Mahalle meydanı su altında ama derinlik az, yetişkin geçebilir.",
        "event_capture": "Yüzeysel su birikintisi.",
        "report_date": ts(78), "created_at": ts(78),
    },
    # Cluster 11: Göç / tahliye noktası
    {
        "id": "aky-091", "report_id": "AKY-RPT-091",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9155, 40.9835]},
        "event_define": "Tahliye noktasında 50'den fazla kişi toplanmış, yiyecek ve su yok.",
        "event_capture": "Toplu tahliye.",
        "report_date": ts(80), "created_at": ts(80),
    },
    {
        "id": "aky-092", "report_id": "AKY-RPT-092",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9158, 40.9838]},
        "event_define": "Tahliye noktasında bebek bezine ihtiyaç var, 5 bebek burada.",
        "event_capture": "Bebek ihtiyacı.",
        "report_date": ts(82), "created_at": ts(82),
    },
    {
        "id": "aky-093", "report_id": "AKY-RPT-093",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9152, 40.9832]},
        "event_define": "Tahliye noktasında ilaç bağımlısı bir diyaliz hastası kritik durumda.",
        "event_capture": "Acil tıbbi ihtiyaç.",
        "report_date": ts(84), "created_at": ts(84),
    },
    # Cluster 12: Araç kurtarma — batı nehir
    {
        "id": "aky-094", "report_id": "AKY-RPT-094",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9035, 40.9808]},
        "event_define": "Araç sele kapıldı devrildi içindeyiz pencereyi kıramıyoruz imdatt.",
        "event_capture": "Araç devrildi.",
        "report_date": ts(86), "created_at": ts(86),
    },
    {
        "id": "aky-095", "report_id": "AKY-RPT-095",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9038, 40.9811]},
        "event_define": "Nehirde bir araç sürükleniyor, içinde kişi var gibi görünüyor, bot lazım.",
        "event_capture": "Araç nehirde.",
        "report_date": ts(88), "created_at": ts(88),
    },
    {
        "id": "aky-096", "report_id": "AKY-RPT-096",
        "user_behavior": "victim", "can_communicate": False,
        "gps_location": {"type": "Point", "coordinates": [37.9032, 40.9805]},
        "event_define": "araba suya daldi biz icindeyiz cam kırılamıo boguluoz.",
        "event_capture": "Boğulma riski.",
        "report_date": ts(90), "created_at": ts(90),
    },
    # Son birkaç tekil yüksek öncelikli
    {
        "id": "aky-097", "report_id": "AKY-RPT-097",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9188, 40.9808]},
        "event_define": "Çöken duvarın altında kalan komşumuzun sesi duyuluyor, kıpırdayamıyor.",
        "event_capture": "Enkaz altında.",
        "report_date": ts(92), "created_at": ts(92),
    },
    {
        "id": "aky-098", "report_id": "AKY-RPT-098",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9210, 40.9822]},
        "event_define": "Köprünün orta ayağı çöktü, üzerindeki minibüs düştü, sürücüden haber yok.",
        "event_capture": "Araç köprüden düştü.",
        "report_date": ts(94), "created_at": ts(94),
    },
    {
        "id": "aky-099", "report_id": "AKY-RPT-099",
        "user_behavior": "victim", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9175, 40.9832]},
        "event_define": "Doğum sancıları başladı, erken doğum riski var, her yol kapalı ambulans yok.",
        "event_capture": "Acil doğum.",
        "report_date": ts(96), "created_at": ts(96),
    },
    {
        "id": "aky-100", "report_id": "AKY-RPT-100",
        "user_behavior": "observer", "can_communicate": True,
        "gps_location": {"type": "Point", "coordinates": [37.9128, 40.9822]},
        "event_define": "Sel suları çekilmeye başladı ama enkaz ve çamur her yerde, bölge geçilemez durumda.",
        "event_capture": "Enkaz temizliği gerekli.",
        "report_date": ts(98), "created_at": ts(98),
    },
]

# ── LLM indicator atamaları (rapor metnine göre deterministik simülasyon) ─────

def assign_indicators(report: dict) -> dict:
    text = (report["event_define"] + " " + report.get("event_capture", "")).lower()
    i = {}
    can = report["can_communicate"]

    # Mahsur / hayati tehlike
    i["people_trapped"] = any(w in text for w in ["mahsur", "çıkamı", "cıkamı", "kurtarın", "imdatt", "kurtarın"])
    i["life_threat"] = any(w in text for w in ["boğul", "bogul", "hayati", "enkaz", "ölecek", "kritik", "patlama riski"])
    i["people_injured"] = any(w in text for w in ["yaralı", "yaralı", "kırıldı", "acı", "ağır"])
    i["children_elderly_at_risk"] = any(w in text for w in ["çocuk", "cocuk", "yaşlı", "yasli", "bebek", "öğrenci"])
    i["large_crowd_affected"] = any(w in text for w in ["kişi", "kisi", "sakin", "toplanan", "50", "15", "35", "20"])

    # Altyapı
    i["building_collapsed"] = any(w in text for w in ["çöktü", "coktu", "yıkıldı", "enkaz", "çökmüş"])
    i["building_damaged"] = any(w in text for w in ["hasar", "eğilme", "çatlak", "bina"])
    i["road_blocked"] = any(w in text for w in ["yol kapandı", "yol kapalı", "yol tıkalı", "geçilemez", "ulaşılamıyor", "ulaşım kesildi", "köprü çöktü", "tünel"])
    i["utility_disrupted"] = any(w in text for w in ["elektrik yok", "elektrikler kesildi", "su kesildi", "jeneratör"])
    i["utility_dangerous"] = any(w in text for w in ["trafo", "yüksek gerilim", "kıvılcım", "gaz sızıntı", "doğalgaz"])

    # Çevre
    i["flood_water_rising"] = any(w in text for w in ["su yükseliyor", "su seviyesi", "taşkın", "sel suları", "dere taştı", "nehir"])
    i["fire_active"] = any(w in text for w in ["yangın", "duman", "yanıyor"])
    i["landslide_active"] = any(w in text for w in ["heyelan", "toprak kaydı", "toprak kayması", "yamaç"])
    i["hazmat_present"] = any(w in text for w in ["gaz kokusu", "kimyasal", "tehlikeli madde", "gaz sızıntısı", "doğalgaz"])
    i["aftershock_risk"] = False

    # İletişim
    i["no_communication"] = not can
    i["area_isolated"] = any(w in text for w in ["izole", "ulaşılamıyor", "bölge yolu kapandı", "her yol kapalı"])
    i["rescue_requested"] = any(w in text for w in ["yardım", "yardm", "kurtarın", "ambulans", "itfaiye", "bot"])

    return i


# ── Disaster type tespiti ─────────────────────────────────────────────────────

def detect_disaster_type(report: dict, indicators: dict) -> str:
    text = (report["event_define"] + " " + report.get("event_capture", "")).lower()
    if indicators.get("fire_active"):
        return "yangın"
    if indicators.get("landslide_active"):
        return "heyelan"
    if indicators.get("flood_water_rising") or "sel" in text or "taşkın" in text:
        return "sel"
    if indicators.get("building_collapsed"):
        return "yapı çöküşü"
    if indicators.get("utility_dangerous") and ("gaz" in text):
        return "gaz sızıntısı"
    if indicators.get("people_injured") and not indicators.get("flood_water_rising"):
        return "tıbbi acil"
    if indicators.get("road_blocked"):
        return "ulaşım engeli"
    return "sel"  # bölge bağlamı: ağırlıklı sel


# ── Ana işlem ─────────────────────────────────────────────────────────────────

def main():
    # 1. Mevcut 50 raporu yükle
    json_path = os.path.join(ROOT, "backend", "seed", "test_reports.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing = data["raw_reports"]  # 50 rapor

    # image_path alanlarını null yap (100 text-only rapor)
    for r in existing:
        r["image_path"] = None
        r["image_location"] = None
        r["image_date"] = None

    # Eğer zaten 100 rapor varsa NEW_REPORTS ekleme
    if len(existing) < 100:
        all_reports = existing + NEW_REPORTS
        data["raw_reports"] = all_reports
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ test_reports.json → {len(all_reports)} rapor")
    else:
        all_reports = existing
        print(f"ℹ️  test_reports.json zaten {len(all_reports)} rapor içeriyor")

    # 3. Her raporu işle
    processed_list = []
    for r in all_reports:
        indicators = assign_indicators(r)
        scoring = calculate_severity(indicators)
        user_status = determine_user_status(indicators, r["can_communicate"])
        action = recommend_action(indicators, scoring["severity_score"])
        disaster_type = detect_disaster_type(r, indicators)

        lon, lat = r["gps_location"]["coordinates"]

        proc = {
            "report_id": r["report_id"],
            "raw_report_id": None,  # DB'de insert sonrası doldurulacak
            "user_behavior": r["user_behavior"],
            "can_communicate": r["can_communicate"],
            "user_status": user_status,
            "gps_location": r["gps_location"],
            "disaster_type": disaster_type,
            "severity_score": scoring["severity_score"],
            "reliability_score": scoring["reliability_score"],
            "category_scores": scoring["category_scores"],
            "active_indicators": scoring["active_indicators"],
            "formula_breakdown": scoring["formula_breakdown"],
            "recommended_action": action,
            "llm_analysis": {
                "model": "simulated",
                "disaster_type": disaster_type,
                "indicators": indicators,
                "reasoning": f"Metin analizi: {r['event_capture']}",
            },
            "vlm_analysis": None,
            "indicators_json": indicators,
            "location_match_score": 1.0,
            "report_date": r["report_date"],
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        processed_list.append(proc)

    # 4. test_reports_processed.json yaz
    output = {
        "region": data["region"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_reports": len(processed_list),
        "processed_reports": processed_list,
    }
    out_path = os.path.join(ROOT, "data", "test_reports_processed.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ test_reports_processed.json → {len(processed_list)} işlenmiş rapor")

    # 5. DB temizle
    print("\n🗑️  DB temizleniyor...")
    cleanup_sql = "TRUNCATE cluster_reports, disaster_clusters, processed_reports, exif_file, raw_reports CASCADE;"
    run_sql(cleanup_sql)

    # 6. raw_reports ekle
    print(f"\n📝 {len(all_reports)} raw_report ekleniyor...")
    for r in all_reports:
        lon2, lat2 = r["gps_location"]["coordinates"]
        wkt = f"SRID=4326;POINT({lon2} {lat2})"
        sql = f"""
INSERT INTO raw_reports (report_id, user_behavior, can_communicate, gps_location,
    event_define, event_capture, report_date, created_at)
VALUES (
    {esc(r['report_id'])}, {esc(r['user_behavior'])}, {boolsql(r['can_communicate'])},
    ST_GeomFromEWKT({esc(wkt)}), {esc(r['event_define'])},
    {esc(r.get('event_capture'))}, {esc(r['report_date'])}, {esc(r['created_at'])}
);
"""
        run_sql(sql)
    print(f"   ✅ {len(all_reports)} raw_report eklendi")

    # 7. processed_reports ekle (raw_report_id join ile)
    print(f"\n⚙️  {len(processed_list)} processed_report ekleniyor...")
    for proc in processed_list:
        lon2 = proc["gps_location"]["coordinates"][0]
        lat2 = proc["gps_location"]["coordinates"][1]
        wkt = f"SRID=4326;POINT({lon2} {lat2})"
        llm_json = json.dumps(proc["llm_analysis"], ensure_ascii=False).replace("'", "''")
        ind_json = json.dumps(proc["indicators_json"], ensure_ascii=False).replace("'", "''")
        sql = f"""
INSERT INTO processed_reports (
    report_id, raw_report_id, user_behavior, can_communicate, user_status,
    gps_location, disaster_type, severity_score, location_match_score,
    report_date, processed_at, llm_analysis, indicators_json
)
SELECT
    {esc(proc['report_id'])},
    r.id,
    {esc(proc['user_behavior'])},
    {boolsql(proc['can_communicate'])},
    {esc(proc['user_status'])},
    ST_GeomFromEWKT({esc(wkt)}),
    {esc(proc['disaster_type'])},
    {proc['severity_score']},
    {proc['location_match_score']},
    {esc(proc['report_date'])},
    {esc(proc['processed_at'])},
    '{llm_json}'::jsonb,
    '{ind_json}'::jsonb
FROM raw_reports r WHERE r.report_id = {esc(proc['report_id'])};
"""
        run_sql(sql)
    print(f"   ✅ {len(processed_list)} processed_report eklendi")

    # 8. Cluster rebuild (DBSCAN ile)
    print("\n🔗 Cluster rebuild tetikleniyor...")
    rebuild_sql = "SELECT COUNT(*) FROM processed_reports;"
    run_sql(rebuild_sql)

    # DBSCAN simülasyonu: Python'da cluster'ları hesapla ve ekle
    _build_clusters_in_db(processed_list)

    print("\n✅ TAMAMLANDI")
    print(f"   📊 {len(all_reports)} raw, {len(processed_list)} processed rapor")
    print(f"   🗄️  DB: raw_reports + processed_reports + disaster_clusters yazıldı")


def _build_clusters_in_db(processed_list: list):
    """DBSCAN ile cluster'ları Python'da hesapla, DB'ye yaz.
    Tüm raporlar tek havuzda kümelenir (tip bazlı gruplama yok).
    eps=0.003 deg (~300m), min_pts=3
    """
    DBSCAN_EPS_DEG = 0.0008  # ~80m — kasıtlı cluster grupları için uygun
    DBSCAN_MIN_PTS = 3

    from collections import defaultdict

    # Tüm raporları düz listeye al
    items = []
    for p in processed_list:
        lon2, lat2 = p["gps_location"]["coordinates"]
        items.append({
            "report_id": p["report_id"],
            "lon": lon2,
            "lat": lat2,
            "severity": p["severity_score"],
            "report_date": p["report_date"],
            "disaster_type": (p["disaster_type"] or "bilinmiyor").lower().strip(),
        })
    items.sort(key=lambda x: x["report_date"])

    grouped = {"all": items}  # tek havuz

    def euclidean(a, b):
        return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

    def region_query(pts, idx, eps):
        return [i for i, p in enumerate(pts) if euclidean(pts[idx], p) < eps]

    def dbscan(pts, eps, min_pts):
        labels = [0] * len(pts)
        cid = 0
        for pi in range(len(pts)):
            if labels[pi] != 0:
                continue
            neighbors = region_query(pts, pi, eps)
            if len(neighbors) < min_pts:
                labels[pi] = -1
                continue
            cid += 1
            labels[pi] = cid
            q = list(neighbors)
            qi = 0
            while qi < len(q):
                curr = q[qi]; qi += 1
                if labels[curr] == -1:
                    labels[curr] = cid
                elif labels[curr] == 0:
                    labels[curr] = cid
                    nn = region_query(pts, curr, eps)
                    if len(nn) >= min_pts:
                        q.extend(nn)
        return labels

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000.0
        dlat = math.radians(lat2-lat1)
        dlon = math.radians(lon2-lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    now_str = datetime.now(timezone.utc).isoformat()
    total_clusters = 0

    for items in grouped.values():
        items.sort(key=lambda x: x["report_date"])
        pts = [(it["lon"], it["lat"]) for it in items]
        labels = dbscan(pts, DBSCAN_EPS_DEG, DBSCAN_MIN_PTS)

        clusters_by_label = defaultdict(list)
        for idx, label in enumerate(labels):
            clusters_by_label[label].append(idx)

        # Noise → singleton
        next_label = max((l for l in clusters_by_label if l > 0), default=0) + 1
        if -1 in clusters_by_label:
            for ni in clusters_by_label[-1]:
                clusters_by_label[next_label] = [ni]
                next_label += 1
            del clusters_by_label[-1]

        for member_idxs in clusters_by_label.values():
            members = [items[i] for i in member_idxs]
            cnt = len(members)
            c_lat = sum(m["lat"] for m in members) / cnt
            c_lon = sum(m["lon"] for m in members) / cnt
            max_dist = max((haversine(c_lat, c_lon, m["lat"], m["lon"]) for m in members), default=0.0)
            radius = min(max(max_dist, 100.0), 300.0)
            sev_vals = [m["severity"] for m in members if m["severity"] is not None]
            sev_avg = round(sum(sev_vals)/len(sev_vals), 1) if sev_vals else None
            first_m = members[0]
            last_m = members[-1]
            is_locked = cnt >= 3
            wkt_center = f"SRID=4326;POINT({c_lon} {c_lat})"
            wkt_init = f"SRID=4326;POINT({first_m['lon']} {first_m['lat']})"

            # Cluster'ın dominant afet tipini üyelerden seç
            type_counts: dict[str, int] = {}
            for m in members:
                t = m["disaster_type"]
                type_counts[t] = type_counts.get(t, 0) + 1
            dominant_type = max(type_counts, key=lambda t: type_counts[t])

            cluster_sql = f"""
WITH new_cluster AS (
  INSERT INTO disaster_clusters
    (cluster_center, initial_center, radius_meters, event_type, report_count,
     severity_avg, first_report_at, last_report_at, is_active, is_locked, created_at, updated_at)
  VALUES (
    ST_GeomFromEWKT({esc(wkt_center)}),
    ST_GeomFromEWKT({esc(wkt_init)}),
    {round(radius, 1)},
    {esc(dominant_type)},
    {cnt},
    {sev_avg if sev_avg is not None else 'NULL'},
    {esc(first_m['report_date'])},
    {esc(last_m['report_date'])},
    TRUE,
    {'TRUE' if is_locked else 'FALSE'},
    {esc(now_str)},
    {esc(now_str)}
  )
  RETURNING id
)
INSERT INTO cluster_reports (cluster_id, report_id, joined_at)
SELECT nc.id, r.id, {esc(now_str)}
FROM new_cluster nc
CROSS JOIN raw_reports r
WHERE r.report_id IN ({','.join(esc(m['report_id']) for m in members)});
"""
            run_sql(cluster_sql)
            total_clusters += 1

    print(f"   ✅ {total_clusters} cluster oluşturuldu")


def esc(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def boolsql(v):
    return "TRUE" if v else "FALSE"


def run_sql(sql: str) -> bool:
    result = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME],
        input=sql, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ SQL HATA:\n{result.stderr}", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    main()
