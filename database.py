
import os

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase


# --------------------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise EnvironmentError("DATABASE_URL environment variable is not set")


try:
    database_url = make_url(DATABASE_URL)

    # Ensure PostgreSQL uses the asyncpg driver.
    if database_url.drivername in ("postgres", "postgresql"):
        database_url = database_url.set(
            drivername="postgresql+asyncpg"
        )

    elif database_url.drivername == "postgresql+asyncpg":
        pass

    else:
        raise ValueError(
            "Unsupported database driver. "
            "Expected PostgreSQL with asyncpg."
        )

    # Clean up parameters that asyncpg does not need.
    query_params = dict(database_url.query)

    query_params.pop("sslmode", None)
    query_params.pop("channel_binding", None)

    # Require SSL unless explicitly configured.
    query_params.setdefault("ssl", "require")

    DATABASE_URL = database_url.set(
        query=query_params
    )

except Exception as exc:
    raise RuntimeError(
        f"Invalid DATABASE_URL configuration: {exc}"
    ) from exc


# --------------------------------------------------
# DATABASE ENGINE
# --------------------------------------------------

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)


# --------------------------------------------------
# SESSION FACTORY
# --------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# --------------------------------------------------
# SQLALCHEMY BASE
# --------------------------------------------------

class Base(DeclarativeBase):
    pass


# --------------------------------------------------
# DATABASE DEPENDENCY
# --------------------------------------------------

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

async def init_db():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


# --------------------------------------------------
# DATABASE SHUTDOWN
# --------------------------------------------------

async def close_db():
    await engine.dispose()
