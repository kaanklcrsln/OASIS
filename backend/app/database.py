from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


# Async PostgreSQL motoru oluştur
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,   # DEBUG=true iken SQL sorgularını terminale yazar
    pool_pre_ping=True,    # Bağlantı kopuksa otomatik yeniler
)

# Her istek için ayrı bir oturum fabrikası
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Tüm modellerin miras alacağı temel sınıf
class Base(DeclarativeBase):
    pass


# FastAPI dependency — her route fonksiyonuna DB oturumu enjekte eder
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
