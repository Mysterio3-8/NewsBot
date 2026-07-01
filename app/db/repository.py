"""Единственная точка доступа к БД. core/ и ui/ не выполняют SQL напрямую."""
from __future__ import annotations

import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, ProcessedPost, PostHistory, RawPost, RejectedPost, Setting, Source
from app.paths import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "app.db"


def make_engine(db_path: Path | None = None):
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


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
        content_hash: int | None = None,
        fetched_at: datetime.datetime | None = None,
    ) -> RawPost:
        with self._session_factory() as session:
            raw_post = RawPost(
                source_id=source_id,
                external_id=external_id,
                raw_text=raw_text,
                media=media,
                content_hash=content_hash,
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

    def get_recent_content_hashes(self, source_id: int, limit: int = 200) -> list[int]:
        with self._session_factory() as session:
            rows = (
                session.query(RawPost.content_hash)
                .filter(RawPost.source_id == source_id, RawPost.content_hash.is_not(None))
                .order_by(RawPost.id.desc())
                .limit(limit)
                .all()
            )
            return [row[0] for row in rows]

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
        status: str = "queued",
    ) -> ProcessedPost:
        with self._session_factory() as session:
            processed = ProcessedPost(
                raw_post_id=raw_post_id,
                score=score,
                category=category,
                rewritten_text=rewritten_text,
                headline=headline,
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

    def list_processed_posts(self, *, status: str | None = None) -> list[ProcessedPost]:
        with self._session_factory() as session:
            query = session.query(ProcessedPost)
            if status is not None:
                query = query.filter(ProcessedPost.status == status)
            return query.order_by(ProcessedPost.score.desc()).all()

    def count_published_since(self, since: datetime.datetime) -> int:
        with self._session_factory() as session:
            return (
                session.query(ProcessedPost)
                .filter(ProcessedPost.status == "published", ProcessedPost.published_at >= since)
                .count()
            )

    def get_raw_post(self, raw_post_id: int) -> RawPost | None:
        with self._session_factory() as session:
            return session.get(RawPost, raw_post_id)
