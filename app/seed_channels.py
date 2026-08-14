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


def normalize_destination(value: str | None) -> str:
    """«club240120678», «-240120678», « 240120678 » → «240120678».

    Сравнивать приёмники как есть нельзя. Один и тот же VK-приёмник записывается
    по-разному: числом, с префиксом `club`/`public`, с минусом (owner_id стены) или с
    случайным пробелом от ручной правки через бота. Разойдись запись хоть на символ —
    и `_ensure_channel` не узнает существующий канал, заведёт ВТОРОЙ и обновит настройки
    ему, а рабочий останется со старыми. Снаружи это выглядит как «сид прогнали, а
    настройки не применились»: ровно такое подозрение возникло 2026-08-12, когда у
    Новостей SEO-теги появились, а у Кино нет."""
    text = (value or "").strip().lstrip("-")
    for prefix in ("club", "public", "event"):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return text[len(prefix):]
    return text


def _ensure_channel(repo: Repository, name: str, *, vk_destination: str, **fields) -> Channel:
    """Идемпотентность по vk_destination (уникальный приёмник), не по имени — чтобы
    переименование канала не плодило дубликат. Имя при этом обновляется.

    Сравнение идёт по НОРМАЛИЗОВАННОМУ приёмнику (см. normalize_destination): иначе
    любая разница в записи одного и того же сообщества плодит канал-двойник."""
    wanted = normalize_destination(vk_destination)
    existing = next(
        (c for c in repo.list_channels() if normalize_destination(c.vk_destination) == wanted),
        None,
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
    # Добавлены 2026-08-14 владельцем, когда прежние четыре канала выработались: каждая
    # неудачная попытка помечает ролик взятым, и за две недели окно поиска выгорело.
    # Все пять проверены живым запросом с сервера — списки роликов читаются.
    "https://www.youtube.com/@Pomidorrro",
    "https://www.youtube.com/@KINO_PORT",
    "https://www.youtube.com/@%D0%9E%D0%91%D0%97%D0%9E%D0%A0%D0%A1%D0%95%D0%A0%D0%98%D0%90%D0%9B%D0%9E%D0%92%D0%98%D0%A4%D0%98%D0%9B%D0%AC%D0%9C%D0%9E%D0%92-%D0%BB6%D1%8C",
    "https://www.youtube.com/@topseriestop",
    "https://www.youtube.com/@watchaction",
]

# Логотип в правом верхнем углу вертикальных клипов (ТЗ 2026-07-21) — тот же кино-логотип,
# что и на фото-постах канала.
CLIP_LOGO_PATH = "assets/filmlogo.png"

# Пулы личных (user) VK-токенов для загрузки медиа. Здесь только ИМЕНА env-переменных —
# сами токены живут в env-файлах (инвариант проекта). Балансер берёт наименее
# нагруженный сегодня аккаунт, поэтому объём делится между ними, а не выжигает один.
# Оба канала берут из ОДНОГО общего пула — это и есть смысл общих счётчиков: нагрузка
# всех четырёх каналов делится между аккаунтами, а не по каналу на аккаунт.
# Значения имён живут в /etc/vk-tokens.env (общий файл на все софты).
# Прежний VK_PHOTO_UPLOAD_TOKEN_KINO (аккаунт 861061590) в пул НЕ входит: 2026-08-03
# проверка показала на нём `user is blocked` во всех четырёх группах — это и есть тот
# самый бан. Вернуть в пул можно только после разблокировки и повторной проверки.
# 🔴 VK_UPLOAD_TOKEN_2 УБРАН 2026-08-14: живая проверка `photos.getWallUploadServer`
# ответила `[5] User authorization failed: user is blocked` — аккаунт забанен. Пока он
# лежал в пуле, балансер честно отдавал ему половину загрузок, и каждая такая
# заканчивалась «не удалось загрузить фото, публикую без него», а пост с медиа
# откладывался (require_media). Отсюда «Кино молчит 16 ч» при исправном софте.
# Вернуть в пул можно только после разблокировки и повторной проверки ТЕМ ЖЕ вызовом.
NEWS_UPLOAD_POOL = ["VK_UPLOAD_TOKEN_1"]
KINO_UPLOAD_POOL = ["VK_UPLOAD_TOKEN_1"]

# Потолок загрузок на ОДИН аккаунт в сутки — грубый предохранитель, не рабочая точка.
# Банит за всплеск, а не за объём (30+ в сутки аккаунт держал штатно). Аккаунт остался
# ОДИН, а спрос всех четырёх софтов — около 34 загрузок в сутки, поэтому 20 упирался бы
# и половина медиа не выходила бы вовсе. Держим 40: предохранитель от всплеска, а не
# суточная норма — от всплеска защищает зазор в 60 минут между загрузками.
VK_TOKEN_DAILY_CAP = 40

# ТЗ 2026-08-03 (после бана личного VK-токена): режем объём до 10 публикаций в сутки —
# 2 фильма + по 2 клипа на фильм + 4 рерайт-поста, разнесённые по всем суткам, чтобы
# фильмы/клипы/рерайты чередовались, а не шли пачкой.
DAILY_PLAN = {
    # ТЗ владельца 2026-08-10: «будет 1 фото с текстом, 3 поста, фильм и 2 клипа».
    "daily_video_count": 1,
    # Зазор между фильмами при одном фильме в сутки роли не играет, но остаётся
    # предохранителем на случай, если счётчик поднимут обратно.
    "daily_video_min_gap_hours": 11,
    "daily_video_min_gap_minutes": 660,
    "daily_clip_count": 2,
    # Рерайт-посты: 4/сутки (ТЗ 2026-08-12: «1 фильм — 2 клипа и 4 поста»).
    # 1440 / 4 = 360 мин — потолок среднего интервала; 260–380 даёт среднее 320 и
    # растягивает посты на все сутки, оставляя промежутки под фильм и клипы.
    "max_posts_per_day": 4,
    "min_interval_minutes": 260,
    "max_interval_minutes": 380,
    # Ночной паузы нет (решение владельца 2026-08-03: «не надо ночную паузу, просто
    # интервал») — равные часы выключают окно в rate_guard.is_quiet_now.
    "quiet_start_hour": 0,
    "quiet_end_hour": 0,
    # Пул личных токенов для загрузки медиа — балансер размазывает 10 загрузок Кино
    # по двум аккаунтам вместо того, чтобы выжигать один (см. VK_UPLOAD_POOL_* ниже).
    "vk_upload_token_envs": KINO_UPLOAD_POOL,
    "vk_token_daily_cap": VK_TOKEN_DAILY_CAP,
    # film_logo_path СНЯТ намеренно (ТЗ 2026-07-28): наложение логотипа на фильм — это
    # полный ре-энкод, ~28 минут CPU и вторая копия файла на диске. При трёх фильмах в
    # сутки на 1-ядерном VPS с 961 МБ RAM это гарантированный OOM и забитый диск.
    # Логотип на КЛИПАХ остаётся (clip_logo_path) — они короткие и лёгкие.
    # Грузить фильмы и клипы на свой YouTube-канал (ТЗ 2026-07-22). Реально работает,
    # только если в .env заданы YT_UPLOAD_* — без них тумблер молчит, остальное идёт.
    "youtube_upload": True,
    # Оригинал из источника, без подмены на сток (ТЗ 2026-07-28: «левые какие-то фото»).
    "stock_fallback": False,
    # Явно фиксируем ветку «фото и описание берём из источника» (ТЗ 2026-08-10).
    # movie_title включил бы поиск кадров в Google вместо кадров самого поста.
    "image_query_mode": "generic",
    # Вторая строка футера TG-поста — ссылка на свою VK-группу.
    "vk_footer_url": "https://vk.com/public240120678",
    # ТЗ 2026-08-12: фильм и клипы ВЕРНУЛИСЬ на стену записями. Разделы «Видео» и
    # «Клипы» пробовали с 2026-08-10 — владелец увидел, что публикаций в ленте стало
    # мало («в кино мало публикаций, нет фильмов и клипов»): ролик, ушедший в каталог
    # сообщества, в ленте не виден вообще, и канал выглядел полупустым.
    # Цена возврата: фильм и клипы снова занимают место на стене — итого 7 записей в
    # сутки (4 поста + фильм + 2 клипа) вместо четырёх.
    "video_as_post": True,
    "max_images_per_post": 1,
    "shuffle_images": True,
    # Кадры источника склеены по два — расклеиваем и берём один случайный.
    "split_collage": True,
    # Чужая жёлтая плашка: переносим вниз кадра и перекрашиваем; не вышло — кадр не берём.
    "promo_banner_mode": "restyle",
    "seo_enabled": True,
    # Короткого имени у сообщества 240120678 нет — тег пойдёт без «@группа». Задашь имя
    # в настройках VK — впиши сюда, и теги станут витриной ТОЛЬКО наших записей
    # (см. docs/SEO.md).
    "seo_hashtag_group": "",
    "seo_base_tags": ["кино", "фильмы", "чтопосмотреть", "новинкикино", "сериалы"],
    "seo_search_phrases": [
        "{q} смотреть онлайн",
        "{q} фильм",
        "{q} трейлер",
        "{q} актёры и роли",
    ],
    # ТЗ владельца 2026-08-11: «чтобы люди писали кино или фильмы — и выдавал мой канал».
    # По теме ищут на порядок чаще, чем по имени актёра, и такой запрос не зависит от
    # того, нашлись ли в тексте имена собственные — поэтому фразы идут в КАЖДОМ ролике.
    "seo_channel_phrases": [
        "кино",
        "фильмы смотреть онлайн",
        "фильмы онлайн бесплатно",
        "что посмотреть вечером",
        "новинки кино 2026",
        "лучшие фильмы смотреть",
        "сериалы смотреть онлайн",
        "кино бесплатно без регистрации",
    ],
    "seo_post_tag_limit": 6,
    "seo_video_tag_limit": 20,
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
        # Явный None снимает логотип с ФИЛЬМА в уже засеянной прод-БД: merge только
        # дописывает ключи, поэтому убрать настройку можно лишь перезаписав её.
        film_logo_path=None,
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
    # ТЗ владельца 2026-08-14: «Новости 5 максимум постов». Смысл не в антибане, а в
    # слотах: аккаунт для загрузки медиа остался ОДИН, зазор между загрузками 60 минут,
    # то есть суточный потолок на все четыре софта — 24 публикации с медиа. Новости на
    # десяти постах съедали почти половину, и фильмам Кино места не оставалось.
    # 24 ч / 5 постов = 288 мин среднего; окно 240-330 даёт ~285 — посты растягиваются
    # на все сутки сами, ограничителем работает лимит, а не интервал.
    "max_posts_per_day": 5,
    "min_interval_minutes": 240,
    "max_interval_minutes": 330,
    # Ночная пауза снята по решению владельца («не надо ночную паузу, просто интервал»).
    # Равные часы = окно выключено (rate_guard.is_quiet_now).
    "quiet_start_hour": 0,
    "quiet_end_hour": 0,
    "vk_upload_token_envs": NEWS_UPLOAD_POOL,
    "vk_token_daily_cap": VK_TOKEN_DAILY_CAP,
}

# Футер со ссылкой на TG-канал СНЯТ 2026-08-12 по ТЗ владельца («убери ссылку, которая
# ведёт на телеграм»). Явные None, а не отсутствие ключей: merge_channel_settings только
# дописывает ключи, и убрать настройку из уже засеянной прод-БД можно лишь перезаписав её.
# Вернуть — поставить обратно URL и подпись «🔢 Новости в трёх словах».
NEWS_FOOTER = {
    "tg_footer_url": None,
    "tg_footer_signature": None,
}

# Режим «простое медиа» (ТЗ 2026-07-27): оригинал фото без замены, все медиа, заголовок
# только на первом и только если на нём мало текста, без лого/фейда.
# ТЗ 2026-08-10: пост с видео на стену не идёт — ролик уходит в раздел «Видео»
# сообщества, вертикальный короткий — в «Клипы». Фото-посты не затронуты.
# ⚠️ У Кино эту настройку 2026-08-12 откатили (в ленте роликов не видно, канал выглядел
# пустым), а Новостей это не касается: владелец сказал «новости всё норм», а видео там
# редкость. Полезут жалобы на пустую ленту — откатывать так же.
NEWS_MEDIA = {"simple_media": True, "video_as_post": False}

# SEO Новостей (ТЗ 2026-08-10). Постоянные теги — общие рубрики канала; поисковые фразы
# работают в описании ролика, у текстовых постов только строка тегов.
NEWS_SEO = {
    "seo_enabled": True,
    "seo_hashtag_group": "",
    "seo_base_tags": ["новости", "новостидня", "происшествия", "чтопроисходит"],
    "seo_search_phrases": [
        "{q} новости",
        "{q} что случилось",
        "{q} последние новости сегодня",
    ],
    # Общие запросы канала (ТЗ 2026-08-11) — по ним ищут постоянно, в отличие от
    # имён собственных конкретной новости.
    "seo_channel_phrases": [
        "новости",
        "новости сегодня",
        "последние новости",
        "новости дня",
        "что случилось сегодня",
        "новости России",
        "срочные новости",
    ],
    "seo_post_tag_limit": 5,
    "seo_video_tag_limit": 18,
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
    merge_channel_settings(
        repo, channel, **NEWS_ANTIBAN, **NEWS_FOOTER, **NEWS_MEDIA, **NEWS_SEO
    )
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
