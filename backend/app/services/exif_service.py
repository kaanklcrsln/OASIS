"""
EXIF Servisi — Görselden EXIF metadata çıkarır.
Pillow kullanarak tarih, GPS, cihaz bilgisi, görsel meta ve ham EXIF verisini döner.
"""
import json
import logging
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _convert_to_degrees(value) -> float:
    """GPS EXIF tuple'ını decimal degrees'e çevir."""
    d, m, s = value
    return float(d) + float(m) / 60 + float(s) / 3600


def _extract_gps(gps_info: dict) -> Optional[dict]:
    """GPS EXIF verisinden lat/lon/altitude/direction çıkar."""
    result = {}
    try:
        if 2 in gps_info and 4 in gps_info:
            lat = _convert_to_degrees(gps_info[2])
            lon = _convert_to_degrees(gps_info[4])
            if gps_info.get(1) == "S":
                lat = -lat
            if gps_info.get(3) == "W":
                lon = -lon
            result["latitude"] = lat
            result["longitude"] = lon

        if 6 in gps_info:
            result["altitude"] = float(gps_info[6])

        if 17 in gps_info:
            result["gps_direction"] = float(gps_info[17])

        if 13 in gps_info:
            result["gps_speed"] = float(gps_info[13])

    except (TypeError, ZeroDivisionError, ValueError):
        pass

    return result if result else None


def extract_exif(image_path: Path) -> dict:
    """
    Görselden tüm faydalı EXIF verisini çıkar.
    Dönen dict exif_file tablosundaki sütunlara 1:1 eşlenir.
    """
    result = {
        "date_taken": None,
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "gps_direction": None,
        "gps_speed": None,
        "device_make": None,
        "device_model": None,
        "image_width": None,
        "image_height": None,
        "orientation": None,
        "flash_fired": None,
        "iso": None,
        "brightness": None,
        "raw_exif": {},
    }

    try:
        img = Image.open(image_path)
        result["image_width"], result["image_height"] = img.size

        exif_data = img._getexif()
        if not exif_data:
            return result

        raw_exif_readable = {}

        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, str(tag_id))

            # JSON serializable yap
            try:
                json.dumps(value)
                raw_exif_readable[tag] = value
            except (TypeError, ValueError):
                raw_exif_readable[tag] = str(value)

            # Tarih
            if tag == "DateTimeOriginal":
                try:
                    result["date_taken"] = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                except (ValueError, TypeError):
                    pass

            # GPS
            if tag == "GPSInfo":
                gps = _extract_gps(value)
                if gps:
                    result.update(gps)

            # Cihaz
            if tag == "Make":
                result["device_make"] = str(value).strip()[:100]
            if tag == "Model":
                result["device_model"] = str(value).strip()[:100]

            # Orientation
            if tag == "Orientation":
                result["orientation"] = int(value) if value else None

            # Flash
            if tag == "Flash":
                result["flash_fired"] = bool(value & 1) if isinstance(value, int) else None

            # ISO
            if tag == "ISOSpeedRatings":
                result["iso"] = int(value) if isinstance(value, (int, float)) else None

            # Brightness
            if tag == "BrightnessValue":
                try:
                    result["brightness"] = float(value)
                except (TypeError, ValueError):
                    pass

        result["raw_exif"] = raw_exif_readable

    except Exception as e:
        logger.warning(f"EXIF çıkarma hatası ({image_path}): {e}")

    return result
