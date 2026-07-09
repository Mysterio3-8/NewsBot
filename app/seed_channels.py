"""Идемпотентный сид каналов мультиканальности. Запускать после деплоя (локально и на
VPS). Создаёт канал + источники, если их ещё нет. Секреты (токены) — только в .env,
здесь лишь ИМЕНА env-переменных (инвариант проекта).

Управление каналами из бота — отдельный срез (5); пока каналы заводятся этим скриптом.
"""
from __future__ import annotations

from app.core.channel_settings import ChannelSettings
from app.db.models import Channel
from app.db.repository import Repository, init_db, make_engine


def _ensure_channel(repo: Repository, name: str, *, vk_destination: str, **fields) -> Channel:
    """Идемпотентность по vk_destination (уникальный приёмник), не по имени — чтобы
    переименование канала не плодило дубликат. Имя при этом обновляется."""
    existing = next(
        (c for c in repo.list_channels() if c.vk_destination == vk_destination), None
    )
    if existing is not None:
        if existing.name != name:
            repo.update_channel(existing.id, name=name)
            print(f"Канал переименован в «{name}» (id={existing.id})")
        else:
            print(f"Канал «{name}» уже есть (id={existing.id})")
        return existing
    channel = repo.create_channel(name=name, vk_destination=vk_destination, **fields)
    print(f"Создан канал «{name}» (id={channel.id})")
    return channel


def _ensure_source(repo: Repository, channel: Channel, *, type: str, name: str, url: str) -> None:
    urls = {s.url for s in repo.list_sources(channel_id=channel.id)}
    if url in urls:
        print(f"  источник {name} ({type} {url}) уже есть")
        return
    repo.create_source(type=type, name=name, url=url, channel_id=channel.id)
    print(f"  добавлен источник {name} ({type} {url})")


def seed_cinema(repo: Repository) -> None:
    """Канал 2 — КиноЛайф. Публикация только в VK (240120678), TG добавим позже. Фильтр
    off (лить всё подряд), лимит 4/день. Источник — VK «Кинопремьеры 2026» (58170807)."""
    channel = _ensure_channel(
        repo,
        "КиноЛайф - Лучшие фильмы",
        vk_destination="240120678",
        vk_token_env="VK_GROUP_TOKEN_KINO",
        settings_json=ChannelSettings(filters_enabled=False, max_posts_per_day=4).to_json(),
    )
    _ensure_source(repo, channel, type="vk", name="Кинопремьеры 2026", url="58170807")


def main() -> None:
    engine = make_engine()
    init_db(engine)
    seed_cinema(Repository(engine))


if __name__ == "__main__":
    main()
