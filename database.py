from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL ="postgresql+asyncpg://gate_db_2eds_user:IzMAsIY98OtYEtdFbCVVtUhKw8JC0vzw@dpg-dbnho1bm8hqs73c8hbtg-a/gate_db_2eds?ssl=true"

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
