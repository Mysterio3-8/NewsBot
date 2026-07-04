"""Проверка чужого водяного знака/логотипа на фото поста перед публикацией
(запрос пользователя 2026-07-05) — если найден чужой брендинг, это фото не
берём в SourceImageProvider, пайплайн сам переходит на сток-фолбэк (Pexels/
Unsplash/Pixabay), как уже сделано для постов без своего фото вообще."""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.llm.client import LLMClient, LLMUnavailableError

logger = logging.getLogger("monitoring")

WATERMARK_CHECK_PROMPT = (
    "Посмотри на фото. Есть ли на нём логотип, водяной знак или текстовая подпись "
    "другого медиа/канала — например, буква или значок в углу, либо надпись вида "
    '"Источник: ..." поверх фото? Ответь ОДНИМ словом: ДА или НЕТ.'
)


def detect_foreign_watermark(client: LLMClient, image_path: Path) -> bool:
    """True — похоже, что на фото чужой водяной знак/логотип, использовать нельзя.
    При недоступности vision (не настроена/сбой сети) — fail-open: считаем, что
    знака нет, чтобы временный сбой проверки не блокировал публикацию фото."""
    try:
        response = client.generate_vision(WATERMARK_CHECK_PROMPT, image_path)
    except LLMUnavailableError:
        logger.warning("Проверка водяного знака недоступна для %s — фото используется как есть", image_path)
        return False
    except Exception:
        logger.exception("Проверка водяного знака упала для %s", image_path)
        return False

    return response.strip().upper().startswith("Д")
