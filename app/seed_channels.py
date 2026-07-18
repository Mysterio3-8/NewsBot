"""Идемпотентный сид каналов мультиканальности. Запускать после деплоя (локально и на
VPS). Создаёт канал + источники, если их ещё нет. Секреты (токены) — только в .env,
здесь лишь ИМЕНА env-переменных (инвариант проекта).

Управление каналами из бота — отдельный срез (5); пока каналы заводятся этим скриптом.
"""
from __future__ import annotations

import json

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
        # Обновляем имя/таргеты существующего канала, но НЕ enabled (чтобы seed не
        # включал намеренно выключенный канал) и НЕ settings_json целиком — на проде
        # настройки канала правились сверх сида (rewrite_kino, weekly_repost, лого...),
        # полная перезапись их бы снесла. Точечные правки — merge_channel_settings.
        fields.pop("settings_json", None)
        repo.update_channel(existing.id, name=name, **fields)
        print(f"Канал «{name}» обновлён (id={existing.id}, enabled/settings не трогаем)")
        return existing
    channel = repo.create_channel(name=name, vk_destination=vk_destination, **fields)
    print(f"Создан канал «{name}» (id={channel.id})")
    return channel


def merge_channel_settings(repo: Repository, channel: Channel, **overrides) -> None:
    """Дописать/обновить ОТДЕЛЬНЫЕ ключи settings_json канала, не трогая остальные —
    безопасно для прод-настроек, которых нет в сиде."""
    data = json.loads(channel.settings_json or "{}")
    data.update(overrides)
    repo.update_channel(channel.id, settings_json=json.dumps(data, ensure_ascii=False))
    print(f"  настройки канала «{channel.name}» дополнены: {overrides}")


def _ensure_source(repo: Repository, channel: Channel, *, type: str, name: str, url: str) -> None:
    urls = {s.url for s in repo.list_sources(channel_id=channel.id)}
    if url in urls:
        print(f"  источник {name} ({type} {url}) уже есть")
        return
    repo.create_source(type=type, name=name, url=url, channel_id=channel.id)
    print(f"  добавлен источник {name} ({type} {url})")


DAILY_VIDEO_SOURCE_GROUP = 223779047  # vkvideo.ru/@club223779047 — фильмы для репоста


def seed_cinema(repo: Repository) -> None:
    """Канал 2 — КиноЛайф. Публикация в VK (240120678) + TG (@kinobestfilmss). Фильтр off
    (лить всё), ссылка на TG в конце поста. Источник — VK «Кинопремьеры 2026» (58170807).
    enabled НЕ включаем здесь. Для существующего канала settings_json НЕ перезаписывается
    целиком (прод-настройки правились сверх сида) — только merge нужных ключей ниже."""
    settings = ChannelSettings(
        filters_enabled=False,
        max_posts_per_day=4,
        min_interval_minutes=300,  # 5ч между постами → 4/день, без пачки
        tg_footer_url="https://t.me/kinobestfilmss",
        daily_video_group=DAILY_VIDEO_SOURCE_GROUP,
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
    # ТЗ 2026-07-18: рерайт-постов 4/день (было 6) + ежедневный видео-репост с нарезкой
    # на клипы из группы-источника фильмов.
    merge_channel_settings(
        repo,
        channel,
        max_posts_per_day=4,
        min_interval_minutes=300,
        daily_video_group=DAILY_VIDEO_SOURCE_GROUP,
    )


def main() -> None:
    engine = make_engine()
    init_db(engine)
    seed_cinema(Repository(engine))


if __name__ == "__main__":
    main()
