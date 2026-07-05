"""Проверка чужого водяного знака/логотипа на фото поста перед публикацией
(запрос пользователя 2026-07-05). Если знак можно убрать простой обрезкой
верхнего и/или нижнего края — используем ЭТО ЖЕ фото, обрезав знак (запрос
пользователя: "хочу чтобы ты в точь в точь находил оригинальное фото только
без чужого монтажа и ватермарков"), не сток-фолбэк. Если знак по центру/на
самом сюжете и обрезкой его не убрать — фото не годится, пайплайн переходит
на сток (Pexels/Unsplash/Pixabay), как и раньше."""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.llm.client import LLMClient, LLMUnavailableError

logger = logging.getLogger("monitoring")

WATERMARK_LOCATE_PROMPT = (
    "Посмотри на фото. Есть ли на нём логотип, водяной знак или текстовая подпись "
    'другого медиа/канала (буква/значок в углу, надпись вида "Источник: ...")?\n'
    "Если знака НЕТ — ответь одним словом: нет\n"
    "Если знак есть и находится ТОЛЬКО у верхнего края фото (полоса или значок "
    "сверху, можно обрезать верхнюю часть и убрать) — ответь: верх\n"
    "Если знак есть и находится ТОЛЬКО у нижнего края — ответь: низ\n"
    "Если есть два знака — один у верхнего края, другой у нижнего — ответь: верх,низ\n"
    "Если знак находится по центру фото, поверх главного объекта, или его нельзя "
    "убрать простой обрезкой сверху/снизу — ответь: не убрать"
)

_EDGE_REGIONS = frozenset({"top", "bottom"})


def locate_foreign_watermark(client: LLMClient, image_path: Path) -> set[str] | None:
    """Возвращает множество краёв, которые нужно обрезать, чтобы убрать чужой
    знак: {"top"}, {"bottom"}, {"top", "bottom"} или пустое множество (знака нет).
    None — знак есть, но обрезкой сверху/снизу его не убрать (см. модуль) — такое
    фото использовать нельзя вообще. При недоступности vision — fail-open (пустое
    множество: считаем, что знака нет, чтобы временный сбой проверки не блокировал
    публикацию фото)."""
    try:
        response = client.generate_vision(WATERMARK_LOCATE_PROMPT, image_path)
    except LLMUnavailableError:
        logger.warning("Проверка водяного знака недоступна для %s — фото используется как есть", image_path)
        return set()
    except Exception:
        logger.exception("Проверка водяного знака упала для %s", image_path)
        return set()

    normalized = response.strip().lower()
    if normalized.startswith("нет"):
        return set()
    if "не убрать" in normalized:
        return None

    regions = {region for region in ("верх", "низ") if region in normalized}
    if not regions:
        # Неожиданный ответ модели — безопаснее считать знак неубираемым, чем
        # рискнуть оставить его на фото.
        return None

    return {"top" if region == "верх" else "bottom" for region in regions}
