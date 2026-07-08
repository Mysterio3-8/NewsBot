"""Ручной конструктор постов для control-бота (аналог «Создать пост» в GRABBER,
запрос пользователя 2026-07-08).

Пользователь присылает текст/медиа → навешивает URL-кнопки → уникализирует текст
(антиплагиат-рерайт через существующий rewrite_post) → публикует в свой канал.

Здесь только ЧИСТАЯ логика (парсинг кнопки, превью) — тестируется без aiogram.
FSM-обвязка и отправка в канал — в control_bot.py (как и остальной бот)."""
from __future__ import annotations

from dataclasses import dataclass

# Telegram позволяет не больше 100 кнопок в разметке, но для поста разумный потолок
# сильно ниже — больше десятка ссылок под постом уже не читаемо.
MAX_BUTTONS = 10


@dataclass(frozen=True)
class PostButton:
    text: str
    url: str


def parse_button_input(raw: str) -> PostButton | None:
    """'Текст | https://ссылка' → PostButton. None, если формат неверный или ссылка
    не http(s) — тогда бот просит прислать заново, а не роняется."""
    if "|" not in raw:
        return None
    text, _, url = raw.partition("|")
    text, url = text.strip(), url.strip()
    if not text or not (url.startswith("http://") or url.startswith("https://")):
        return None
    return PostButton(text=text, url=url)


def render_preview(text: str, buttons: list[PostButton]) -> str:
    """Текстовое превью черновика для владельца (что именно уйдёт в канал)."""
    lines = [text.strip() or "(без текста)"]
    if buttons:
        lines.append("")
        lines.append("Кнопки под постом:")
        for button in buttons:
            lines.append(f"• {button.text} → {button.url}")
    return "\n".join(lines)
