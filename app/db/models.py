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


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(10))  # "tg" | "vk"
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(500))
    priority: Mapped[int] = mapped_column(Integer, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

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


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
