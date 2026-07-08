"""Единственная точка доступа к БД. core/ и ui/ не выполняют SQL напрямую."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, ProcessedPost, PostHistory, RawPost, RejectedPost, Setting, Source
from app.paths import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "app.db"

_UINT64_BOUNDARY = 1 << 63
_UINT64_MODULUS = 1 << 64


def _to_signed_64(value: int) -> int:
    """SimHash отдаёт беззнаковое 64-битное число, а SQLite INTEGER — знаковый
    64-битный (sqlite3 бросает OverflowError на значениях >= 2**63)."""
    return value - _UINT64_MODULUS if value >= _UINT64_BOUNDARY else value


def _from_signed_64(value: int) -> int:
    return value + _UINT64_MODULUS if value < 0 else value


def make_engine(db_path: Path | None = None):
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(engine) -> None:
    """`create_all` создаёт только отсутствующие таблицы, не меняет существующие —
    поэтому дополнительно доливаем колонки, добавленные в модели позже (лёгкая
    миграция без Alembic, т.к. схема пока меняется нечасто).
    """
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type = column.type.compile(engine.dialect)
                connection.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}")
                )
        connection.commit()


class Repository:
    """Обёртка над SQLAlchemy Session с операциями предметной области."""

    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(bind=engine)

    def create_source(self, *, type: str, name: str, url: str, priority: int = 5) -> Source:
        with self._session_factory() as session:
            source = Source(type=type, name=name, url=url, priority=priority, enabled=True)
            session.add(source)
            session.commit()
            session.refresh(source)
            return source

    def list_sources(self, *, source_type: str | None = None) -> list[Source]:
        with self._session_factory() as session:
            query = session.query(Source)
            if source_type is not None:
                query = query.filter(Source.type == source_type)
            return query.all()

    def get_source(self, source_id: int) -> Source | None:
        with self._session_factory() as session:
            return session.get(Source, source_id)

    def update_source(self, source_id: int, **fields) -> None:
        with self._session_factory() as session:
            session.query(Source).filter(Source.id == source_id).update(fields)
            session.commit()

    def delete_source(self, source_id: int) -> None:
        with self._session_factory() as session:
            session.query(Source).filter(Source.id == source_id).delete()
            session.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            return setting.value if setting else default

    def set_setting(self, key: str, value: str) -> None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            if setting is None:
                session.add(Setting(key=key, value=value))
            else:
                setting.value = value
            session.commit()

    def create_raw_post(
        self,
        *,
        source_id: int,
        external_id: str,
        raw_text: str,
        media: str | None = None,
        video_path: str | None = None,
        content_hash: int | None = None,
        fetched_at: datetime.datetime | None = None,
    ) -> RawPost:
        with self._session_factory() as session:
            raw_post = RawPost(
                source_id=source_id,
                external_id=external_id,
                raw_text=raw_text,
                media=media,
                video_path=video_path,
                content_hash=_to_signed_64(content_hash) if content_hash is not None else None,
                fetched_at=fetched_at or datetime.datetime.utcnow(),
            )
            session.add(raw_post)
            session.commit()
            session.refresh(raw_post)
            return raw_post

    def get_existing_external_ids(self, source_id: int) -> set[str]:
        with self._session_factory() as session:
            rows = session.query(RawPost.external_id).filter(RawPost.source_id == source_id).all()
            return {row[0] for row in rows}

    def get_max_external_id(self, source_id: int) -> int | None:
        """Максимальный числовой external_id уже обработанных постов источника —
        для инициализации курсора мониторинга (fetch_new_posts) из истории, когда
        курсор в settings ещё не сохранён (напр. после перехода со старой схемы по
        времени). None, если постов нет или id не числовые."""
        with self._session_factory() as session:
            rows = session.query(RawPost.external_id).filter(RawPost.source_id == source_id).all()
        ids = [int(row[0]) for row in rows if row[0] and str(row[0]).lstrip("-").isdigit()]
        return max(ids) if ids else None

    def get_recent_content_hashes(self, source_id: int, limit: int = 200) -> list[int]:
        with self._session_factory() as session:
            rows = (
                session.query(RawPost.content_hash)
                .filter(RawPost.source_id == source_id, RawPost.content_hash.is_not(None))
                .order_by(RawPost.id.desc())
                .limit(limit)
                .all()
            )
            return [_from_signed_64(row[0]) for row in rows]

    def create_rejected_post(
        self, *, raw_post_id: int, reason: str, score: float = 0.0
    ) -> RejectedPost:
        with self._session_factory() as session:
            rejected = RejectedPost(raw_post_id=raw_post_id, reason=reason, score=score)
            session.add(rejected)
            session.commit()
            session.refresh(rejected)
            return rejected

    def create_processed_post(
        self,
        *,
        raw_post_id: int,
        score: float,
        category: str | None = None,
        rewritten_text: str | None = None,
        headline: str | None = None,
        image_paths: list[str] | None = None,
        video_path: str | None = None,
        status: str = "queued",
    ) -> ProcessedPost:
        with self._session_factory() as session:
            processed = ProcessedPost(
                raw_post_id=raw_post_id,
                score=score,
                category=category,
                rewritten_text=rewritten_text,
                headline=headline,
                image_paths=json.dumps(image_paths) if image_paths else None,
                video_path=video_path,
                status=status,
            )
            session.add(processed)
            session.commit()
            session.refresh(processed)
            self.add_post_history(post_id=processed.id, status=status)
            return processed

    def add_post_history(self, *, post_id: int, status: str, note: str | None = None) -> None:
        with self._session_factory() as session:
            session.add(PostHistory(post_id=post_id, status=status, note=note))
            session.commit()

    def update_processed_post_status(
        self, post_id: int, status: str, *, published_at: datetime.datetime | None = None
    ) -> None:
        with self._session_factory() as session:
            fields: dict = {"status": status}
            if published_at is not None:
                fields["published_at"] = published_at
            session.query(ProcessedPost).filter(ProcessedPost.id == post_id).update(fields)
            session.commit()
        self.add_post_history(post_id=post_id, status=status)

    def get_processed_post(self, post_id: int) -> ProcessedPost | None:
        with self._session_factory() as session:
            return session.get(ProcessedPost, post_id)

    def mark_published(
        self, post_id: int, network: str, published_at: datetime.datetime
    ) -> None:
        """Помечает публикацию в КОНКРЕТНУЮ сеть (network="tg"/"vk") — раздельно от
        общего published_at/status, чтобы rate_guard мог отличить законный кросс-пост
        (уже ушло в TG, теперь публикуем в VK) от повторной попытки в ТУ ЖЕ сеть."""
        column = f"published_{network}_at"
        with self._session_factory() as session:
            fields: dict = {column: published_at, "status": "published"}
            existing = session.get(ProcessedPost, post_id)
            if existing is not None and existing.published_at is None:
                fields["published_at"] = published_at
            session.query(ProcessedPost).filter(ProcessedPost.id == post_id).update(fields)
            session.commit()
        self.add_post_history(post_id=post_id, status="published")

    def get_published_network_at(
        self, post_id: int, network: str
    ) -> datetime.datetime | None:
        post = self.get_processed_post(post_id)
        if post is None:
            return None
        return getattr(post, f"published_{network}_at", None)

    def list_processed_posts(self, *, status: str | None = None) -> list[ProcessedPost]:
        with self._session_factory() as session:
            query = session.query(ProcessedPost)
            if status is not None:
                query = query.filter(ProcessedPost.status == status)
            return query.order_by(ProcessedPost.score.desc()).all()

    def list_fresh_queued_posts(self, cutoff: datetime.datetime) -> list[ProcessedPost]:
        """Посты в очереди со свежестью не старше cutoff, самые СВЕЖИЕ первыми
        (по created_at desc). Используется для публикации только свежих новостей —
        см. scheduler.pick_next_post_to_publish."""
        with self._session_factory() as session:
            return (
                session.query(ProcessedPost)
                .filter(
                    ProcessedPost.status == "queued",
                    ProcessedPost.created_at >= cutoff,
                )
                .order_by(ProcessedPost.created_at.desc())
                .all()
            )

    def expire_stale_queued_posts(self, cutoff: datetime.datetime) -> int:
        """Помечает залежавшиеся посты очереди (created_at < cutoff) как 'expired',
        чтобы старые новости никогда не публиковались и очередь не копилась. Возвращает
        число протухших."""
        with self._session_factory() as session:
            stale = (
                session.query(ProcessedPost)
                .filter(
                    ProcessedPost.status == "queued",
                    ProcessedPost.created_at < cutoff,
                )
                .all()
            )
            for post in stale:
                post.status = "expired"
            count = len(stale)
            session.commit()
        return count

    def list_recent_published(self, limit: int = 10) -> list[ProcessedPost]:
        with self._session_factory() as session:
            return (
                session.query(ProcessedPost)
                .filter(ProcessedPost.status == "published")
                .order_by(ProcessedPost.published_at.desc())
                .limit(limit)
                .all()
            )

    def count_published_since(self, since: datetime.datetime) -> int:
        with self._session_factory() as session:
            return (
                session.query(ProcessedPost)
                .filter(ProcessedPost.status == "published", ProcessedPost.published_at >= since)
                .count()
            )

    def get_last_published_at(self) -> datetime.datetime | None:
        with self._session_factory() as session:
            row = (
                session.query(ProcessedPost.published_at)
                .filter(
                    ProcessedPost.status == "published",
                    ProcessedPost.published_at.is_not(None),
                )
                .order_by(ProcessedPost.published_at.desc())
                .first()
            )
            return row[0] if row else None

    def get_raw_post(self, raw_post_id: int) -> RawPost | None:
        with self._session_factory() as session:
            return session.get(RawPost, raw_post_id)
