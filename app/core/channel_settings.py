"""Настройки канала из Channel.settings_json (мультиканальность).

Переопределяют глобальные дефолты config.yaml для конкретного канала. Все поля
опциональны — дефолт/None означает «наследовать глобальную настройку». Хранятся в БД
как JSON (Channel.settings_json), чтобы добавлять настройки без миграций схемы.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


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
            image_query_mode=data.get("image_query_mode", "generic"),
            image_providers_order=data.get("image_providers_order"),
            logo_path=data.get("logo_path"),
            photo_design=data.get("photo_design"),
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
            film_logo_path=data.get("film_logo_path"),
            film_blur_region=data.get("film_blur_region"),
            film_blur_strength=data.get("film_blur_strength", 20),
            max_interval_minutes=data.get("max_interval_minutes"),
        )

    def to_json(self) -> str:
        payload: dict = {"filters_enabled": self.filters_enabled}
        if self.max_posts_per_day is not None:
            payload["max_posts_per_day"] = self.max_posts_per_day
        if self.min_interval_minutes is not None:
            payload["min_interval_minutes"] = self.min_interval_minutes
        if self.tg_footer_url is not None:
            payload["tg_footer_url"] = self.tg_footer_url
        if self.image_query_mode != "generic":
            payload["image_query_mode"] = self.image_query_mode
        if self.image_providers_order is not None:
            payload["image_providers_order"] = self.image_providers_order
        if self.logo_path is not None:
            payload["logo_path"] = self.logo_path
        if self.photo_design is not None:
            payload["photo_design"] = self.photo_design
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
        if self.film_logo_path is not None:
            payload["film_logo_path"] = self.film_logo_path
        if self.film_blur_region is not None:
            payload["film_blur_region"] = self.film_blur_region
        if self.film_blur_strength != 20:
            payload["film_blur_strength"] = self.film_blur_strength
        if self.max_interval_minutes is not None:
            payload["max_interval_minutes"] = self.max_interval_minutes
        return json.dumps(payload, ensure_ascii=False)
