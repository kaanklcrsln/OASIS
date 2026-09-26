import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine, Base
from app.routes.reports import router as reports_router
from app.routes.clusters import router as clusters_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("oasis")


# ── Uygulama başlarken ve kapanırken çalışan yaşam döngüsü ──────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Başlangıç: Tablolar yoksa otomatik oluştur
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Veritabanı tabloları hazır")
    if not settings.GEMINI_API_KEY:
        logger.warning(
            "⚠️ GEMINI_API_KEY tanımlı değil — raporlar kaydedilir ama AI analizi yapılmaz. "
            ".env dosyasına anahtarınızı ekleyip API'yi yeniden başlatın."
        )
    yield
    # Kapatma: Motor bağlantılarını kapat
    await engine.dispose()
    logger.info("🛑 Veritabanı bağlantıları kapatıldı")


# ── FastAPI uygulaması ───────────────────────────────────────────────────────
app = FastAPI(
    title="OASIS API",
    description="Afet yönetimi ve acil yardım talepleri API'si",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — mobil uygulama ve dashboard'ın istek atabilmesi için
origins = ["*"] if settings.ALLOWED_ORIGINS == "*" else settings.ALLOWED_ORIGINS.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Route'ları kaydet ────────────────────────────────────────────────────────
app.include_router(reports_router, prefix="/api")
app.include_router(clusters_router, prefix="/api")

# ── Görselleri statik olarak sun ─────────────────────────────────────────────
settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(settings.IMAGES_DIR)), name="images")
# Reverse proxy'lerin (nginx, Cloudflare tunnel) /api/* kuralı ile de erişilebilsin
app.mount("/api/images", StaticFiles(directory=str(settings.IMAGES_DIR)), name="api_images")


# ── Sağlık kontrolü ─────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check():
    """API'nin çalışıp çalışmadığını kontrol et."""
    return {"status": "ok", "version": "1.0.0", "ai_enabled": bool(settings.GEMINI_API_KEY)}
