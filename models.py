from sqlalchemy import String, Integer, DateTime, func, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    zone: Mapped[str] = mapped_column(String(10), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    photo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())   