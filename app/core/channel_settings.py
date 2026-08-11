"""Настройки канала из Channel.settings_json (мультиканальность).

Переопределяют глобальные дефолты config.yaml для конкретного канала. Все поля
опциональны — дефолт/None означает «наследовать глобальную настройку». Хранятся в БД
как JSON (Channel.settings_json), чтобы добавлять настройки без миграций схемы.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core.seo.builder import SeoProfile


@dataclass(frozen=True)
class ChannelSettings:
    filters_enabled: bool = True
    """False → «лить всё подряд» (кино/мемы): пропускаем LLM-гейт новостей и порог
    min_score, оставляем только дедуп (не публиковать один пост дважды). True → полная
    новостная фильтрация, как у Канала 1."""

    max_posts_per_day: int | None = None
    """None → глобальный лимит из config. Иначе — свой дневной лимит канала."""

    min_interval_minutes: int | None = None
    """Минимум минут между публикациями канала (защита от пачки). None → глобальный."""

    tg_footer_url: str | None = None
    """Ссылка, добавляемая в конец поста этого канала (напр. ссылка на TG-канал). None → нет."""

    tg_footer_signature: str | None = None
    """Подпись гиперссылки футера в TG (напр. «🔢 Новости в трёх словах» / «🎬 Больше
    фильмов»). None → брендовая подпись из config.footer. Актуально только если задан
    tg_footer_url."""

    image_query_mode: str = "generic"
    """"generic" → обычный сток-запрос по смыслу текста (Pexels/Unsplash/Pixabay).
    "movie_title" → LLM извлекает НАЗВАНИЕ ФИЛЬМА из текста, ищем реальные кадры/постеры
    (кино-канал) через image_providers_order (обычно ["google"]), а не сток."""

    image_providers_order: list[str] | None = None
    """Переопределяет глобальный images.providers_order для этого канала. None →
    глобальный порядок. Обязателен при image_query_mode="movie_title" (сток не найдёт
    кадры конкретного фильма)."""

    logo_path: str | None = None
    """Свой логотип-вотермарк канала (напр. "assets/filmlogo.png" для Кино). None →
    глобальный logo из watermark-конфига (новостной)."""

    photo_design: bool | None = None
    """Новостное оформление (зелёный fade + заголовок, headline_card) для этого канала.
    None → наследовать глобальный тумблер. False → выключить (напр. Кино — свой стиль,
    без новостного шаблона). True → включить принудительно."""

    simple_media: bool = False
    """Режим «простое медиа» (ТЗ 2026-07-27, Новости): брать ОРИГИНАЛЬНЫЕ фото поста без
    замены (детектор чужих знаков не подменяет фото на сток), публиковать ВСЕ фото,
    накладывать заголовок-хук ТОЛЬКО на первое фото и только если на нём мало текста
    (иначе буквы смешаются — vision-проверка). Без логотипа и без цветного фейда. Если
    у поста нет своего фото — добавить один сток по смыслу + заголовок."""

    rewrite_prompt: str | None = None
    """Имя своего промпта рерайта канала (напр. "rewrite_kino" — красивый подробный
    кино-стиль). None → новостной "rewrite"."""

    rewrite_max_length: int | None = None
    """Абсолютный потолок длины рерайта (знаков). None → длина оригинала (как у Новостей)."""

    rewrite_length_factor: float | None = None
    """Длина рерайта = длина оригинала × factor (напр. 1.3 = «чуть больше оригинала»
    для Кино). None → берётся rewrite_max_length или длина оригинала. Приоритетнее
    rewrite_max_length."""

    split_collage: bool = False
    """Расклеивать склеенные кадры (кино: 2 кадра в одном фото → 2 отдельных). None/False
    → не трогать (Новости — фото источника цельные)."""

    uniquify_images: bool = False
    """Уникализация картинок (антиплагиат: микро-кроп + шум + чистка EXIF). Кино → True."""

    weekly_repost: bool = False
    """Раз в неделю перезаливать лучший пост канала (по просмотрам+лайкам VK за 7 дней).
    Кино → True."""

    daily_video_group: int | None = None
    """VK group_id источника для ежедневного видео-репоста. Резервный путь — запрос
    пользователя 2026-07-18: VK жёстко троттлит видео-CDN для датацентр-IP VPS (скорость
    падает до единиц КБ/с при обычном канале VPS 200+ МБ/с), поэтому основной источник —
    daily_video_youtube_channels. None → VK-путь не используется."""

    daily_video_youtube_channels: list[str] = field(default_factory=list)
    """Список YouTube-каналов (URL, напр. "https://www.youtube.com/@mmalive1830") —
    основной источник ежедневного видео-репоста. Проверяются по порядку, берётся первое
    ещё не публиковавшееся видео самого свежего из них. Пусто → YouTube-путь не
    используется (тогда фолбэк на daily_video_group, если задан)."""

    daily_clip_count: int = 3
    """Сколько вертикальных клипов нарезать из ежедневного видео."""

    daily_clip_seconds: int = 35
    """Длительность одного клипа, секунд."""

    daily_clip_min_gap_seconds: int = 120
    """Минимальный зазор между клипами внутри фильма (и от ранее нарезанных участков)."""

    clip_logo_path: str | None = None
    """Логотип-вотермарк в правом верхнем углу клипа (напр. "assets/filmlogo.png").
    None → клипы режутся без оформления (логотипа и хука)."""

    daily_video_count: int = 1
    """Сколько видео публиковать за сутки (ТЗ 2026-07-21: Кино → 2)."""

    daily_video_min_gap_hours: int = 5
    """Минимум часов между двумя видео одних суток — чтобы фильмы не шли подряд."""

    daily_video_start_hour_utc: int | None = None
    """С какого часа UTC можно публиковать фильмы. None → дефолт 8 (11:00 МСК).
    При большом daily_video_count нужен 0: счётчик суток обнуляется в полночь UTC,
    поэтому старт в 08:00 UTC при зазоре 60 мин вмещает максимум 16 фильмов, не 24."""

    vk_upload_token_envs: list[str] = field(default_factory=list)
    """Пул ИМЁН env-переменных с личными VK-токенами для загрузки медиа (балансер).
    Пусто → используется одиночный Channel.vk_upload_token_env, как раньше. Несколько
    токенов нужны при большом объёме: 24 фильма/сутки на один личный токен = бан."""

    vk_token_daily_cap: int | None = None
    """Сколько загрузок в сутки допускать на ОДИН токен пула. None → дефолт балансера."""

    require_media: bool = True
    """Пост, у которого ЕСТЬ медиа, публиковать только вместе с медиа.

    Личный токен для загрузки берётся из пула и может быть занят (зазор между
    загрузками одного аккаунта). Раньше в этом случае публикатор молча уходил в
    best-effort и постил голый текст — владелец 2026-08-04: «в кино только какие
    посты странные текстовые без фото и фильма, так не надо это всё портит».
    Теперь такой пост остаётся в очереди и выйдет следующим циклом, когда аккаунт
    освободится. Пост, у которого медиа не было изначально, флаг не трогает."""

    daily_video_min_gap_minutes: int | None = None
    """Зазор между фильмами в МИНУТАХ. Приоритетнее daily_video_min_gap_hours — нужен
    при большом daily_video_count (24 фильма/сутки не влезают в целочасовой зазор).
    None → берётся daily_video_min_gap_hours × 60."""

    @property
    def video_gap_minutes(self) -> int:
        """Фактический зазор между фильмами в минутах (минуты перекрывают часы)."""
        if self.daily_video_min_gap_minutes is not None:
            return self.daily_video_min_gap_minutes
        return self.daily_video_min_gap_hours * 60

    film_logo_path: str | None = None
    """Логотип в правом верхнем углу САМОГО ФИЛЬМА (не клипа). None → фильм публикуется
    как скачан, без перекодирования. ВНИМАНИЕ: наложение требует полного ре-энкода —
    на 1-ядерном VPS это ~50 минут CPU на трёхчасовой фильм."""

    film_blur_region: list[float] | None = None
    """Область чужого водяного знака в долях кадра [x, y, ширина, высота] (напр.
    [0.82, 0.04, 0.15, 0.10] — правый верхний угол). Замыливается перед публикацией
    тем же проходом, что и наложение логотипа. None → ничего не блюрим."""

    film_blur_strength: int = 20
    """Сила размытия области водяного знака (радиус boxblur). Больше — сильнее."""

    max_interval_minutes: int | None = None
    """Верхняя граница случайного интервала между публикациями. Задан вместе с
    min_interval_minutes → пауза выбирается случайно в [min, max] (ТЗ 2026-07-21:
    1.5-4 часа на рандом). None → интервал ровно min_interval_minutes."""

    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None
    """Ночная пауза в МСК (антибан VK): в окне [start, end) публикация не идёт. Окно
    может пересекать полночь (напр. 0..7). Оба None → пауза выключена."""

    youtube_upload: bool = False
    """Грузить ли фильмы и клипы на свой YouTube-канал (ТЗ 2026-07-22). Работает только
    если в .env заданы YT_UPLOAD_* — иначе тумблер ни на что не влияет. Кино → True."""

    stock_fallback: bool = True
    """Подставлять ли сток-картинку, когда своего фото у поста нет или все отфильтрованы.
    Кино → False (ТЗ 2026-07-28: «оригинал из источника, без замены») — сток по смыслу
    текста давал картинки, не относящиеся к фильму."""

    vk_footer_url: str | None = None
    """Ссылка на свою VK-группу для футера TG-поста («Больше контента в нашем VK»).
    None → в футере только TG-ссылка, как раньше."""

    video_as_post: bool = True
    """Публиковать ли ролик записью на стене.

    False (ТЗ владельца 2026-08-10) → фильм уходит в раздел «Видео», клип — в «Клипы»,
    записи на стене не создаётся. Стена остаётся под текстовые посты. True → прежнее
    поведение: ролик + запись с вложением."""

    shuffle_images: bool = False
    """Перемешивать порядок фото поста случайно (ТЗ 2026-08-10). Источник отдаёт кадры
    в одном и том же порядке — на дистанции лента выглядит однообразно."""

    max_images_per_post: int | None = None
    """Потолок числа фото в посте. Кино → 1 («будет 1 фото с текстом»). None → как было
    (все свои фото до MAX_SOURCE_PHOTOS)."""

    promo_banner_mode: str = "drop"
    """Что делать с чужой ярко-жёлтой промо-плашкой на кадре:
    "drop" — кадр не берём совсем (прежнее поведение);
    "restyle" — плашку переносим вниз кадра и перекрашиваем (фон и буквы), кадр
    остаётся в посте. Не удалось переоформить — молча падаем в "drop"."""

    seo_enabled: bool = False
    """Собирать хэштеги/поисковые описания для публикаций канала."""

    seo_hashtag_group: str = ""
    """Короткое имя сообщества VK для тега `#ключ@имя` (напр. «kinobestfilmss»).
    Пусто → теги без привязки к сообществу."""

    seo_base_tags: list[str] = field(default_factory=list)
    """Постоянные теги канала — идут первыми в каждой публикации."""

    seo_search_phrases: list[str] = field(default_factory=list)
    """Шаблоны поисковых фраз с `{q}` для описаний роликов («{q} смотреть онлайн»)."""

    seo_channel_phrases: list[str] = field(default_factory=list)
    """Постоянные запросы сообщества («фильмы смотреть онлайн», «новости сегодня») —
    идут в описании КАЖДОГО ролика независимо от текста поста (ТЗ 2026-08-11)."""

    seo_post_tag_limit: int = 5
    seo_video_tag_limit: int = 20
    """Сколько тегов вешать на запись стены и на ролик. У ролика описание свёрнуто,
    поэтому там теги можно не экономить."""

    def seo_profile(self, links: list[str] | None = None) -> "SeoProfile":
        """Настройки канала → профиль SEO-сборщика. Ссылки приходят снаружи: футер
        собирается из tg/vk-полей канала и знать про них SEO-слою незачем."""
        return SeoProfile(
            hashtag_group=self.seo_hashtag_group,
            base_tags=list(self.seo_base_tags),
            search_phrases=list(self.seo_search_phrases),
            channel_phrases=list(self.seo_channel_phrases),
            post_tag_limit=self.seo_post_tag_limit,
            video_tag_limit=self.seo_video_tag_limit,
            links=list(links or []),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> "ChannelSettings":
        if not raw:
            return cls()
        data = json.loads(raw)
        return cls(
            filters_enabled=data.get("filters_enabled", True),
            max_posts_per_day=data.get("max_posts_per_day"),
            min_interval_minutes=data.get("min_interval_minutes"),
            tg_footer_url=data.get("tg_footer_url"),
            tg_footer_signature=data.get("tg_footer_signature"),
            image_query_mode=data.get("image_query_mode", "generic"),
            image_providers_order=data.get("image_providers_order"),
            logo_path=data.get("logo_path"),
            photo_design=data.get("photo_design"),
            simple_media=data.get("simple_media", False),
            rewrite_prompt=data.get("rewrite_prompt"),
            rewrite_max_length=data.get("rewrite_max_length"),
            rewrite_length_factor=data.get("rewrite_length_factor"),
            split_collage=data.get("split_collage", False),
            uniquify_images=data.get("uniquify_images", False),
            weekly_repost=data.get("weekly_repost", False),
            daily_video_group=data.get("daily_video_group"),
            daily_video_youtube_channels=data.get("daily_video_youtube_channels", []),
            daily_clip_count=data.get("daily_clip_count", 3),
            daily_clip_seconds=data.get("daily_clip_seconds", 35),
            daily_clip_min_gap_seconds=data.get("daily_clip_min_gap_seconds", 120),
            clip_logo_path=data.get("clip_logo_path"),
            daily_video_count=data.get("daily_video_count", 1),
            daily_video_min_gap_hours=data.get("daily_video_min_gap_hours", 5),
            daily_video_min_gap_minutes=data.get("daily_video_min_gap_minutes"),
            daily_video_start_hour_utc=data.get("daily_video_start_hour_utc"),
            vk_upload_token_envs=data.get("vk_upload_token_envs", []),
            vk_token_daily_cap=data.get("vk_token_daily_cap"),
            require_media=data.get("require_media", True),
            film_logo_path=data.get("film_logo_path"),
            film_blur_region=data.get("film_blur_region"),
            film_blur_strength=data.get("film_blur_strength", 20),
            max_interval_minutes=data.get("max_interval_minutes"),
            quiet_start_hour=data.get("quiet_start_hour"),
            quiet_end_hour=data.get("quiet_end_hour"),
            youtube_upload=data.get("youtube_upload", False),
            stock_fallback=data.get("stock_fallback", True),
            vk_footer_url=data.get("vk_footer_url"),
            video_as_post=data.get("video_as_post", True),
            shuffle_images=data.get("shuffle_images", False),
            max_images_per_post=data.get("max_images_per_post"),
            promo_banner_mode=data.get("promo_banner_mode", "drop"),
            seo_enabled=data.get("seo_enabled", False),
            seo_hashtag_group=data.get("seo_hashtag_group", ""),
            seo_base_tags=data.get("seo_base_tags", []),
            seo_search_phrases=data.get("seo_search_phrases", []),
            seo_channel_phrases=data.get("seo_channel_phrases", []),
            seo_post_tag_limit=data.get("seo_post_tag_limit", 5),
            seo_video_tag_limit=data.get("seo_video_tag_limit", 20),
        )

    def to_json(self) -> str:
        payload: dict = {"filters_enabled": self.filters_enabled}
        if self.max_posts_per_day is not None:
            payload["max_posts_per_day"] = self.max_posts_per_day
        if self.min_interval_minutes is not None:
            payload["min_interval_minutes"] = self.min_interval_minutes
        if self.tg_footer_url is not None:
            payload["tg_footer_url"] = self.tg_footer_url
        if self.tg_footer_signature is not None:
            payload["tg_footer_signature"] = self.tg_footer_signature
        if self.image_query_mode != "generic":
            payload["image_query_mode"] = self.image_query_mode
        if self.image_providers_order is not None:
            payload["image_providers_order"] = self.image_providers_order
        if self.logo_path is not None:
            payload["logo_path"] = self.logo_path
        if self.photo_design is not None:
            payload["photo_design"] = self.photo_design
        if self.simple_media:
            payload["simple_media"] = True
        if self.rewrite_prompt is not None:
            payload["rewrite_prompt"] = self.rewrite_prompt
        if self.rewrite_max_length is not None:
            payload["rewrite_max_length"] = self.rewrite_max_length
        if self.rewrite_length_factor is not None:
            payload["rewrite_length_factor"] = self.rewrite_length_factor
        if self.split_collage:
            payload["split_collage"] = True
        if self.uniquify_images:
            payload["uniquify_images"] = True
        if self.weekly_repost:
            payload["weekly_repost"] = True
        if self.daily_video_group is not None:
            payload["daily_video_group"] = self.daily_video_group
        if self.daily_video_youtube_channels:
            payload["daily_video_youtube_channels"] = self.daily_video_youtube_channels
        if self.daily_clip_count != 3:
            payload["daily_clip_count"] = self.daily_clip_count
        if self.daily_clip_seconds != 35:
            payload["daily_clip_seconds"] = self.daily_clip_seconds
        if self.daily_clip_min_gap_seconds != 120:
            payload["daily_clip_min_gap_seconds"] = self.daily_clip_min_gap_seconds
        if self.clip_logo_path is not None:
            payload["clip_logo_path"] = self.clip_logo_path
        if self.daily_video_count != 1:
            payload["daily_video_count"] = self.daily_video_count
        if self.daily_video_min_gap_hours != 5:
            payload["daily_video_min_gap_hours"] = self.daily_video_min_gap_hours
        if self.daily_video_min_gap_minutes is not None:
            payload["daily_video_min_gap_minutes"] = self.daily_video_min_gap_minutes
        if self.daily_video_start_hour_utc is not None:
            payload["daily_video_start_hour_utc"] = self.daily_video_start_hour_utc
        if self.vk_upload_token_envs:
            payload["vk_upload_token_envs"] = self.vk_upload_token_envs
        if self.vk_token_daily_cap is not None:
            payload["vk_token_daily_cap"] = self.vk_token_daily_cap
        if not self.require_media:
            payload["require_media"] = self.require_media
        if self.film_logo_path is not None:
            payload["film_logo_path"] = self.film_logo_path
        if self.film_blur_region is not None:
            payload["film_blur_region"] = self.film_blur_region
        if self.film_blur_strength != 20:
            payload["film_blur_strength"] = self.film_blur_strength
        if self.max_interval_minutes is not None:
            payload["max_interval_minutes"] = self.max_interval_minutes
        if self.quiet_start_hour is not None:
            payload["quiet_start_hour"] = self.quiet_start_hour
        if self.quiet_end_hour is not None:
            payload["quiet_end_hour"] = self.quiet_end_hour
        if self.youtube_upload:
            payload["youtube_upload"] = True
        if not self.stock_fallback:
            payload["stock_fallback"] = False
        if self.vk_footer_url is not None:
            payload["vk_footer_url"] = self.vk_footer_url
        if not self.video_as_post:
            payload["video_as_post"] = False
        if self.shuffle_images:
            payload["shuffle_images"] = True
        if self.max_images_per_post is not None:
            payload["max_images_per_post"] = self.max_images_per_post
        if self.promo_banner_mode != "drop":
            payload["promo_banner_mode"] = self.promo_banner_mode
        if self.seo_enabled:
            payload["seo_enabled"] = True
        if self.seo_hashtag_group:
            payload["seo_hashtag_group"] = self.seo_hashtag_group
        if self.seo_base_tags:
            payload["seo_base_tags"] = self.seo_base_tags
        if self.seo_search_phrases:
            payload["seo_search_phrases"] = self.seo_search_phrases
        if self.seo_channel_phrases:
            payload["seo_channel_phrases"] = self.seo_channel_phrases
        if self.seo_post_tag_limit != 5:
            payload["seo_post_tag_limit"] = self.seo_post_tag_limit
        if self.seo_video_tag_limit != 20:
            payload["seo_video_tag_limit"] = self.seo_video_tag_limit
        return json.dumps(payload, ensure_ascii=False)
