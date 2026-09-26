"""
Image Servisi — Görseli sıkıştırıp IMAGES_DIR (varsayılan /app/images) altına kaydeder.
Orijinal dosyadan EXIF çıkarıldıktan SONRA sıkıştırma yapılır.
"""
import uuid
import shutil
import logging
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings

logger = logging.getLogger(__name__)

IMAGES_DIR = settings.IMAGES_DIR
MAX_DIMENSION = 1920
JPEG_QUALITY = 85


def save_original_temp(upload_file, original_filename: str) -> Path:
    """Upload'ı geçici dosyaya kaydet (EXIF çıkarma için orijinal gerekli)."""
    today = datetime.utcnow()
    save_dir = IMAGES_DIR / str(today.year) / f"{today.month:02d}"
    save_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(original_filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"

    filename = f"{uuid.uuid4().hex}{ext}"
    temp_path = save_dir / f"_orig_{filename}"

    with open(temp_path, "wb") as f:
        shutil.copyfileobj(upload_file, f)

    return temp_path


def compress_and_save(temp_path: Path) -> Path:
    """
    Geçici dosyayı sıkıştırıp kalıcı dosya olarak kaydet.
    Orijinal (temp) silinir, sıkıştırılmış dosya döner.
    Ortalama: 5MB → 200-400KB
    """
    final_path = temp_path.parent / temp_path.name.replace("_orig_", "")

    try:
        img = Image.open(temp_path)

        # EXIF orientation düzelt
        img = ImageOps.exif_transpose(img) or img

        # Resize (max 1920px)
        if max(img.size) > MAX_DIMENSION:
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

        # RGBA → RGB (PNG alpha channel)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # JPEG olarak kaydet
        img.save(final_path, "JPEG", quality=JPEG_QUALITY, optimize=True)

        # Orijinal temp dosyayı sil
        temp_path.unlink(missing_ok=True)

        logger.info(f"Görsel sıkıştırıldı: {final_path} ({final_path.stat().st_size / 1024:.0f} KB)")

    except Exception as e:
        logger.warning(f"Sıkıştırma hatası: {e} — orijinal kullanılıyor")
        if temp_path.exists():
            temp_path.rename(final_path)

    return final_path


def get_relative_path(full_path: Path) -> str:
    """Docker volume path'ini relative path'e çevir (DB'ye kaydetmek için)."""
    try:
        return str(full_path.relative_to(IMAGES_DIR))
    except ValueError:
        return str(full_path)
