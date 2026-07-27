"""Идемпотентный сид каналов мультиканальности. Запускать после деплоя (локально и на
VPS). Создаёт канал + источники, если их ещё нет. Секреты (токены) — только в .env,
здесь лишь ИМЕНА env-переменных (инвариант проекта).

Управление каналами из бота — отдельный срез (5); пока каналы заводятся этим скриптом.
"""
from __future__ import annotations

import json

from app.core.channel_settings import ChannelSettings
from app.db.models import Channel
from app.db.repository import DEFAULT_CHANNEL_NAME, Repository, init_db, make_engine


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


# Живой прогон 2026-07-18 показал: VK жёстко троттлит видео-CDN для датацентр-IP VPS
# (скорость падает до единиц КБ/с при обычном канале VPS 200+ МБ/с) — докачка фильма
# растягивалась бы на многие часы. Источник видео переключён на YouTube пользователя.
# Проверяются по порядку — первое ещё не публиковавшееся видео самого свежего канала.
DAILY_VIDEO_YOUTUBE_CHANNELS = [
    # ТЗ 2026-07-21: «Сериальный Календарь» — приоритетный источник, оба дневных фильма
    # берутся с него, пока на нём есть неопубликованные видео.
    "https://www.youtube.com/channel/UCck_kyGeLhKGJb225ERbzag",
    "https://www.youtube.com/@mmalive1830",
    "https://www.youtube.com/@KINOTEKA-2030",
    "https://www.youtube.com/@%D0%9A%D0%B8%D0%BD%D0%BE%D0%BA%D0%BE%D1%82-%D1%818%D0%BF",
]

# Логотип в правом верхнем углу вертикальных клипов (ТЗ 2026-07-21) — тот же кино-логотип,
# что и на фото-постах канала.
CLIP_LOGO_PATH = "assets/filmlogo.png"

# ТЗ 2026-07-21: 10 публикаций в сутки — 2 фильма + 4 клипа (по 2 на фильм) + 4 рерайта,
# пауза между рерайт-постами случайная 1.5-4 часа, круглосуточно.
DAILY_PLAN = {
    "daily_video_count": 2,
    "daily_video_min_gap_hours": 5,
    "daily_clip_count": 2,
    "max_posts_per_day": 4,
    "min_interval_minutes": 90,
    "max_interval_minutes": 240,
    # Логотип на самом фильме. ВНИМАНИЕ: включает полный ре-энкод (~50 мин CPU на
    # трёхчасовой фильм на 1-ядерном VPS). Снять — убрать этот ключ.
    "film_logo_path": "assets/filmlogo.png",
    # Грузить фильмы и клипы на свой YouTube-канал (ТЗ 2026-07-22). Реально работает,
    # только если в .env заданы YT_UPLOAD_* — без них тумблер молчит, остальное идёт.
    "youtube_upload": True,
}


def seed_cinema(repo: Repository) -> None:
    """Канал 2 — КиноЛайф. Публикация в VK (240120678) + TG (@kinobestfilmss). Фильтр off
    (лить всё), ссылка на TG в конце поста. Источник — VK «Кинопремьеры 2026» (58170807).
    enabled НЕ включаем здесь. Для существующего канала settings_json НЕ перезаписывается
    целиком (прод-настройки правились сверх сида) — только merge нужных ключей ниже."""
    settings = ChannelSettings(
        filters_enabled=False,
        tg_footer_url="https://t.me/kinobestfilmss",
        tg_footer_signature="🎬 Больше фильмов",
        daily_video_youtube_channels=DAILY_VIDEO_YOUTUBE_CHANNELS,
        clip_logo_path=CLIP_LOGO_PATH,
        **DAILY_PLAN,
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
    # на клипы. daily_video_group снят (2026-07-18, замена на YouTube — см. выше).
    merge_channel_settings(
        repo,
        channel,
        daily_video_group=None,
        daily_video_youtube_channels=DAILY_VIDEO_YOUTUBE_CHANNELS,
        clip_logo_path=CLIP_LOGO_PATH,
        tg_footer_signature="🎬 Больше фильмов",
        **DAILY_PLAN,
    )


# Канал 1 «Новости» → публикует в TG @NewsThreeWord + VK 233689032 (таргеты заполняет
# headless из config.publishing при старте, здесь их не дублируем). Единственный источник —
# «прямой эфир» (novosti_efir), остальные исторические источники канала гасим (ТЗ 2026-07-26:
# «брать только с одного канала»).
NEWS_SOURCE_URL = "https://t.me/novosti_efir"

# Антибан VK (all_auto/CLAUDE.md): объём заметно ниже 50/сут, случайный интервал, ночная
# пауза 6-8ч в МСК. Публикация в VK-группу использует ЛИЧНЫЙ токен для загрузки фото —
# самый банимый, поэтому консервативно. quiet 0..7 МСК = 7ч тишины. Значения per-channel
# (settings_json) перекрывают глобальные 999/10 из config.yaml — иначе Новости постили бы
# без лимита (ban-risk). Меняются без кода: повторный прогон сида или правка в боте.
NEWS_ANTIBAN = {
    "max_posts_per_day": 20,
    "min_interval_minutes": 40,
    "max_interval_minutes": 75,
    "quiet_start_hour": 0,
    "quiet_end_hour": 7,
}

# Ссылка на свой TG-канал в конце каждого поста (ТЗ 2026-07-27). TG-рендер — брендовая
# гиперссылка с этой подписью; VK — призыв подписаться + голый кликабельный URL.
NEWS_FOOTER = {
    "tg_footer_url": "https://t.me/NewsThreeWord",
    "tg_footer_signature": "🔢 Новости в трёх словах",
}


def _keep_only_source(repo: Repository, channel: Channel, keep_url: str) -> None:
    """Оставить включённым только источник keep_url, остальные источники канала выключить
    (не удаляем — чтобы легко вернуть, просто гасим enabled). Идемпотентно."""
    for source in repo.list_sources(channel_id=channel.id):
        should_be_on = source.url == keep_url
        if bool(source.enabled) != should_be_on:
            repo.update_source(source.id, enabled=should_be_on)
            print(f"  источник {source.name}: enabled={should_be_on}")


def seed_news(repo: Repository) -> None:
    """Канал «Новости» → включить (VK + TG), сузить источники до одного «прямого эфира»,
    выставить консервативный антибан. Идемпотентно. Канал создаётся при init_db
    (_ensure_default_channel), здесь только правим состояние."""
    channel = next(
        (c for c in repo.list_channels() if c.name == DEFAULT_CHANNEL_NAME), None
    )
    if channel is None:
        print("Канал «Новости» не найден (создаётся при init_db) — пропуск")
        return
    repo.update_channel(channel.id, enabled=True)
    merge_channel_settings(repo, channel, **NEWS_ANTIBAN, **NEWS_FOOTER)
    _keep_only_source(repo, channel, NEWS_SOURCE_URL)
    print(f"Канал «Новости» включён (id={channel.id}), источник — только {NEWS_SOURCE_URL}")


def main() -> None:
    engine = make_engine()
    init_db(engine)
    repo = Repository(engine)
    seed_cinema(repo)
    seed_news(repo)


if __name__ == "__main__":
    main()
