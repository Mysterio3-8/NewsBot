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
        # Обновляем настройки/таргеты существующего канала, но НЕ enabled — чтобы seed
        # не включал канал, намеренно выключенный (напр. кино до готовности монтажа).
        repo.update_channel(existing.id, name=name, **fields)
        print(f"Канал «{name}» обновлён (id={existing.id}, enabled не трогаем)")
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
    """Канал 2 — КиноЛайф. Публикация в VK (240120678) + TG (@kinobestfilmss). Фильтр off
    (лить всё), 3 поста/день с интервалом 8ч (не пачкой), ссылка на TG в конце поста.
    Источник — VK «Кинопремьеры 2026» (58170807). enabled НЕ включаем здесь — канал
    остаётся выключенным до готовности своего монтажа/поиска фото."""
    settings = ChannelSettings(
        filters_enabled=False,
        max_posts_per_day=3,
        min_interval_minutes=480,  # 8ч между постами → 3/день, без пачки
        tg_footer_url="https://t.me/kinobestfilmss",
    )
    channel = _ensure_channel(
        repo,
        "КиноЛайф - Лучшие фильмы",
        vk_destination="240120678",
        vk_token_env="VK_GROUP_TOKEN_KINO",
        tg_token_env="TG_BOT_TOKEN",
        tg_destination="@kinobestfilmss",
        settings_json=settings.to_json(),
    )
    _ensure_source(repo, channel, type="vk", name="Кинопремьеры 2026", url="58170807")


def main() -> None:
    engine = make_engine()
    init_db(engine)
    seed_cinema(Repository(engine))


if __name__ == "__main__":
    main()
