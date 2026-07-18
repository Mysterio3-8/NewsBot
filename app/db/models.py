"""SQLAlchemy модели. Схема — раздел 14 SPEC.md."""
from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Channel(Base):
    """Один целевой канал/паблик пользователя (новости / кино / городской / мемы...).
    Мультиканальность: каждый канал — свой набор источников, свои таргеты публикации и
    свой блок настроек (фильтр/оформление/лимит) в settings_json. AppConfig из config.yaml
    задаёт ДЕФОЛТЫ, канал их переопределяет. Секреты не хранятся — только ИМЕНА env-
    переменных (token_env), сами токены в .env (инвариант проекта)."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Таргет Telegram: имя env с токеном бота + назначение (@канал или chat_id).
    tg_token_env: Mapped[str] = mapped_column(String(100), default="TG_BOT_TOKEN")
    tg_destination: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Таргет VK: имя env с групповым токеном + group_id + опц. личный upload-токен
    # (group-токен не грузит фото/видео, VK error 27 — см. CLAUDE.md).
    vk_token_env: Mapped[str] = mapped_column(String(100), default="VK_GROUP_TOKEN")
    vk_destination: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vk_upload_token_env: Mapped[str] = mapped_column(
        String(100), default="VK_PHOTO_UPLOAD_TOKEN"
    )

    # Блок настроек канала (фильтр on/off, оформление, лимит, стиль хуков) — JSON, чтобы
    # добавлять настройки без миграций схемы. Пусто = наследуем дефолты из config.yaml.
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    sources: Mapped[list["Source"]] = relationship(back_populates="channel")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # nullable: старые источники, созданные до мультиканальности, привязываются к
    # «Каналу 1» миграцией ensure_default_channel при старте.
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(10))  # "tg" | "vk"
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(500))
    priority: Mapped[int] = mapped_column(Integer, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    channel: Mapped["Channel"] = relationship(back_populates="sources")
    raw_posts: Mapped[list["RawPost"]] = relationship(back_populates="source")


class RawPost(Base):
    __tablename__ = "raw_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text)
    media: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # локальный путь к скачанному видео
    content_hash: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    source: Mapped["Source"] = relationship(back_populates="raw_posts")
    processed: Mapped[list["ProcessedPost"]] = relationship(back_populates="raw_post")
    rejections: Mapped[list["RejectedPost"]] = relationship(back_populates="raw_post")


class ProcessedPost(Base):
    __tablename__ = "processed_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rewritten_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_paths: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-список путей
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # путь к видео с watermark
    status: Mapped[str] = mapped_column(String(50), default="new")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # Раздельно от общего published_at/status — иначе rate_guard не может отличить
    # "уже ушло в TG, теперь публикуем в VK" (законный кросс-пост) от "уже ушло в TG,
    # повторно пытаемся ТУДА ЖЕ" (баг, который на проде 2026-07-05 разослал один и тот
    # же пост 28 раз подряд в один канал — check_publish_allowed видел status='published'
    # и пропускал ЛЮБУЮ повторную попытку без проверки конкретной сети).
    published_tg_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    published_vk_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # ID поста на стене VK (из wall.post) — нужен для еженедельного репоста лучшего поста:
    # по нему читаем просмотры+лайки через wall.getById (запрос пользователя 2026-07-14).
    vk_post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw_post: Mapped["RawPost"] = relationship(back_populates="processed")
    history: Mapped[list["PostHistory"]] = relationship(back_populates="post")


class PostHistory(Base):
    __tablename__ = "post_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("processed_posts.id"))
    status: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    post: Mapped["ProcessedPost"] = relationship(back_populates="history")


class RejectedPost(Base):
    __tablename__ = "rejected_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"))
    reason: Mapped[str] = mapped_column(String(255))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    raw_post: Mapped["RawPost"] = relationship(back_populates="rejections")


class RepostedVideo(Base):
    """Видео из группы-источника, уже опубликованное в наш канал ежедневным
    видео-джобом (защита от повторной публикации). video_ref = "owner_id_video_id"."""

    __tablename__ = "reposted_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    video_ref: Mapped[str] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class ClipSegment(Base):
    """Клип, нарезанный из видео ежедневного репоста. Интервалы (start/end) хранятся,
    чтобы повторная нарезка того же видео не пересекалась с уже созданными клипами.
    scheduled_at/published_at — план публикации по дню: публикатор клипов (interval-джоб)
    находит due-клипы по scheduled_at и постит их — план переживает рестарт сервиса."""

    __tablename__ = "clip_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    video_ref: Mapped[str] = mapped_column(String(100))
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    clip_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
