import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise EnvironmentError("DATABASE_URL not set")

# Render injects: postgresql://user:pass@internal-host:5432/db?sslmode=require
# asyncpg needs:  postgresql+asyncpg://user:pass@internal-host:5432/db?ssl=require

# Fix 1: Add asyncpg driver if missing
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://"):]

# Fix 2: asyncpg uses "ssl" not "sslmode"
DATABASE_URL = DATABASE_URL.replace("sslmode=require", "ssl=require")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   