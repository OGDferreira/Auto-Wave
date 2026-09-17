import os
from datetime import datetime

from sqlalchemy import DateTime, Float, JSON, String, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    meta_app_id: Mapped[str] = mapped_column(String(255), nullable=True, default="")
    meta_app_secret: Mapped[str] = mapped_column(String(255), nullable=True, default="")
    meta_webhook_verify_token: Mapped[str] = mapped_column(String(255), nullable=True, default="")
    vapid_public_key: Mapped[str] = mapped_column(String(500), nullable=True, default="")
    vapid_private_key: Mapped[str] = mapped_column(String(1000), nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    meta_access_token: Mapped[str] = mapped_column(String(500), nullable=True, default="")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pendente")
    views_count: Mapped[int] = mapped_column(default=0)
    leads_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(500), nullable=False)
    auth: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharkbotEvent(Base):
    __tablename__ = "sharkbot_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, default="lead")
    valor: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    raw_payload: Mapped[str] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./auto_wave.db")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_dependency():
    async with AsyncSessionLocal() as session:
        yield session
