"""Сборка SEO-обвязки публикации: хэштеги, поисковый хвост поста, описание ролика.

Что именно индексируется (и почему код устроен так, а не иначе):

* **Запись на стене VK** — текст целиком, но в выдачу сообщества человек приходит по
  ХЭШТЕГУ. Тег вида `#роберт_дауни@kinobestfilmss` привязан к сообществу: тап по нему
  показывает только наши записи. Поэтому у поста тегов немного и они точные — простыня
  тегов под коротким постом читается как спам и роняет вовлечённость.
* **Видеозапись/клип VK** — отдельно индексируются НАЗВАНИЕ и ОПИСАНИЕ ролика, причём
  описание в ленте свёрнуто. Отсюда прямое ТЗ владельца 2026-08-10: «у них можешь
  описание делать большое, потому что его не видно будет, а постов небольшое маленькое
  делай». Большое описание ролика — бесплатное место под ключи.
* **Google/Яндекс** — видят публичные страницы `vk.com/wall...`, `vkvideo.ru/...` и
  `t.me/s/<канал>`. Им важны первые ~150 знаков и наличие запроса целой фразой, поэтому
  строка запросов собирается человеческими фразами («Роберт Дауни смотреть онлайн»),
  а не списком отдельных слов.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.seo.keywords import extract_entities, extract_keywords, normalize

VK_POST_LIMIT = 16000
VK_VIDEO_DESCRIPTION_LIMIT = 4000
"""Запас к реальному потолку VK (~5000): описание склеивается из частей, и обрезка на
границе слова важнее, чем последние сто знаков хвоста."""

_TAG_JUNK = re.compile(r"[^\w]", re.UNICODE)
_UNDERSCORE_RUN = re.compile(r"_{2,}")


@dataclass(frozen=True)
class SeoProfile:
    """Постоянные настройки SEO одного канала. Всё, что меняется без правки кода."""

    hashtag_group: str = ""
    """Короткое имя сообщества VK для тега `#ключ@имя`. Пусто → тег без привязки."""

    base_tags: list[str] = field(default_factory=list)
    """Постоянные теги канала («кино», «фильмы2026»). Идут первыми, всегда."""

    search_phrases: list[str] = field(default_factory=list)
    """Шаблоны поисковых фраз с `{q}` — «{q} смотреть онлайн», «{q} трейлер».
    Подставляется имя собственное из текста. Пусто → строка запросов не собирается."""

    post_tag_limit: int = 5
    """Сколько тегов вешать на запись стены. Больше — выглядит спамом."""

    video_tag_limit: int = 20
    """Сколько тегов вешать на ролик. Описание свёрнуто, спамом не выглядит."""

    links: list[str] = field(default_factory=list)
    """Строки-ссылки в конец описания ролика («🎬 Больше фильмов: https://...»)."""


def build_tags(text: str, profile: SeoProfile, limit: int) -> list[str]:
    """Хэштеги от самых ценных к общим: имена → постоянные канала → тематические слова.

    Имена собственные идут ПЕРВЫМИ намеренно. Это то, что человек реально набирает в
    поиске («Роберт Дауни»), и ровно ради них всё затевалось. Постоянные теги канала
    ставить вперёд нельзя: их пять, а лимит поста — шесть, и на имя не осталось бы
    места ни разу.

    Дедуп идёт по нормализованному слагу, поэтому «Роберт Дауни» и «роберт дауни» не
    дадут двух тегов, а base_tags канала не продублируются найденным словом."""
    if limit <= 0:
        return []

    # Частотные слова в теги НЕ идут. Они не различают части речи, и в ленту лезли
    # «#играет», «#потерявшего», «#сообщил» — по таким никто не ищет, а выглядят они
    # как накрутка. Для поисковых фраз (build_search_line) слова по-прежнему годятся:
    # там они попадают внутрь человеческой фразы, а не висят отдельным тегом.
    seen: set[str] = set()
    tags: list[str] = []
    for source in (extract_entities(text), profile.base_tags):
        for item in source:
            slug = _slugify(item)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            tags.append(f"#{slug}@{profile.hashtag_group}" if profile.hashtag_group else f"#{slug}")
            if len(tags) >= limit:
                return tags
    return tags


def build_post_seo_tail(text: str, profile: SeoProfile) -> str:
    """Хвост записи на стене: одна строка тегов, без «простыни ключей».

    Осознанно НЕ добавляем сюда список поисковых фраз — под коротким постом это шум
    для живого читателя, а вся тяжёлая SEO-артиллерия уезжает в описание ролика."""
    tags = build_tags(text, profile, profile.post_tag_limit)
    return " ".join(tags)


def build_search_line(text: str, profile: SeoProfile) -> str:
    """Строка поисковых фраз для описания ролика: «Роберт Дауни смотреть онлайн · ...».

    Фраза целиком, а не набор слов: внешние поисковики ранжируют точное вхождение
    запроса выше, чем совпадение отдельных слов вразнобой."""
    if not profile.search_phrases:
        return ""
    subjects = extract_entities(text, limit=3) or extract_keywords(text, limit=2)
    if not subjects:
        return ""
    phrases = [
        template.format(q=subject)
        for subject in subjects
        for template in profile.search_phrases
    ]
    return " · ".join(dict.fromkeys(phrases))


def build_video_seo_description(
    *,
    title: str,
    body: str,
    profile: SeoProfile,
    limit: int = VK_VIDEO_DESCRIPTION_LIMIT,
) -> str:
    """Большое описание видеозаписи/клипа: сюжет → поисковые фразы → ссылки → теги.

    Порядок неслучаен. Первые ~150 знаков попадают в сниппет поисковика — там должен
    стоять человеческий текст с названием, а не список тегов. Теги в самом низу: они
    нужны внутреннему поиску VK, которому позиция в тексте безразлична."""
    source = f"{title}\n{body}"
    blocks = [
        title.strip(),
        body.strip(),
        build_search_line(source, profile),
        "\n".join(profile.links),
        " ".join(build_tags(source, profile, profile.video_tag_limit)),
    ]
    return _fit(("\n\n".join(block for block in blocks if block.strip())).strip(), limit)


def _slugify(phrase: str) -> str:
    """«Роберт Дауни» → «роберт_дауни». В теге VK живут только буквы, цифры и `_`."""
    slug = _UNDERSCORE_RUN.sub("_", _TAG_JUNK.sub("", normalize(phrase).replace(" ", "_")))
    return slug.strip("_")


def _fit(text: str, limit: int) -> str:
    """Обрезка по границе слова — обрубленное посередине слово в сниппете выглядит
    как поломка сайта и по нему уже ничего не найдётся."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.8 else cut).rstrip() + "…"
