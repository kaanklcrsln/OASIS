from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Değerler sırasıyla ortam değişkenlerinden, repo kökündeki .env'den
    # ve (varsa) backend/.env'den okunur. Docker'da compose değişkenleri aktarır.
    model_config = SettingsConfigDict(
        env_file=(_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL bağlantı URL'i (asyncpg sürücüsü ile)
    DATABASE_URL: str = "postgresql+asyncpg://oasis_user:oasis_secret@localhost:5432/oasis"

    # API sunucu ayarları
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = False

    # CORS — virgülle ayrılmış origin listesi veya "*"
    ALLOWED_ORIGINS: str = "*"

    # Google AI Studio anahtarı (eski isim GOOGLE_AI_STUDIO_API_KEY de desteklenir)
    GEMINI_API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY"),
    )
    CLUSTER_RADIUS_METERS: float = 500.0

    # Yüklenen görsellerin saklandığı dizin (Docker'da ./uploads buraya bağlanır)
    IMAGES_DIR: Path = Path("/app/images")
    # Test verisi (test_reports.json + örnek görseller)
    SEED_DIR: Path = _BACKEND_DIR / "seed"


# Uygulama genelinde kullanılacak tek ayar nesnesi
settings = Settings()
