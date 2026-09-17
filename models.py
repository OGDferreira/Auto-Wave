import os
import socket
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import Boolean, DateTime, Float, JSON, String, func
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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(nullable=False, index=True)
    media_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    caption: Mapped[str] = mapped_column(String(2200), nullable=True, default="")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="agendado")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def build_database_url() -> tuple[str, dict]:
    configured_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./auto_wave.db").strip()
    if configured_url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        parsed = urlsplit(configured_url)
        database_url = urlunsplit(
            (
                "postgresql+asyncpg",
                parsed.netloc,
                parsed.path,
                "",
                "",
            )
        )
        connect_args = {
            "family": socket.AF_INET,
            "ssl": "require",
            "timeout": 15,
        }
        if parsed.port == 6543:
            connect_args["statement_cache_size"] = 0
        return database_url, connect_args

    return configured_url, {}


DATABASE_URL, ENGINE_CONNECT_ARGS = build_database_url()

engine = create_async_engine(
    DATABASE_URL,
    connect_args=ENGINE_CONNECT_ARGS,
    echo=False,
    future=True,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def database_target() -> dict[str, str]:
    parsed = urlsplit(DATABASE_URL)
    return {
        "driver": parsed.scheme,
        "host": parsed.hostname or "",
        "port": str(parsed.port or ""),
        "database": parsed.path.lstrip("/") or "",
    }


async def session_dependency():
    async with AsyncSessionLocal() as session:
        yield session
