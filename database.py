import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise EnvironmentError("DATABASE_URL not set")

# Strip any sslmode param and replace with ssl
DATABASE_URL = DATABASE_URL.replace("sslmode=require", "ssl=require")
DATABASE_URL = DATABASE_URL.replace("sslmode=prefer", "ssl=prefer")
DATABASE_URL = DATABASE_URL.replace("&sslmode=require", "")
DATABASE_URL = DATABASE_URL.replace("?sslmode=require", "")

# Remove channel_binding (asyncpg doesn't support it)
DATABASE_URL = DATABASE_URL.replace("&channel_binding=require", "")
DATABASE_URL = DATABASE_URL.replace("?channel_binding=require", "")

# Ensure asyncpg driver
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://"):]

# Add ssl if no ssl param exists
if "ssl" not in DATABASE_URL.split("?")[-1] if "?" in DATABASE_URL else True:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&ssl=require"
    else:
        DATABASE_URL += "?ssl=require"

print(f"🔗 Connecting to: {DATABASE_URL.split('@')[-1]}")

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