"""Кнопочный интерфейс control-бота в стиле GRABBER (запрос пользователя 2026-07-08:
«перевести софт в удобного кнопочного бота как на скринах»).

Reply-клавиатура снизу = главное меню, инлайн-подменю по разделам. Здесь только
СБОРКА клавиатур (тестируется структурой); обработка нажатий (callback-роутинг и
FSM) — в control_bot.py. Тексты reply-кнопок вынесены в константы: по ним же
control_bot ловит нажатия, поэтому строки должны совпадать один-в-один."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_NEW_POST = "📝 Создать пост"
BTN_AUTOPOSTING = "🤖 Автопостинг"
BTN_CHANNELS = "📺 Каналы"
BTN_SOURCES = "📰 Источники"
BTN_SETTINGS = "⚙️ Настройки"
BTN_TOOLS = "🧰 Инструменты"
BTN_STATUS = "📊 Статус"

MAIN_MENU_BUTTONS = (
    BTN_NEW_POST,
    BTN_AUTOPOSTING,
    BTN_CHANNELS,
    BTN_SOURCES,
    BTN_SETTINGS,
    BTN_TOOLS,
    BTN_STATUS,
)


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню снизу — две колонки, как в GRABBER."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW_POST), KeyboardButton(text=BTN_AUTOPOSTING)],
            [KeyboardButton(text=BTN_CHANNELS), KeyboardButton(text=BTN_SOURCES)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_TOOLS)],
            [KeyboardButton(text=BTN_STATUS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def channels_menu(channels) -> InlineKeyboardMarkup:
    """Список каналов — каждая кнопка открывает карточку канала."""
    rows = [
        [InlineKeyboardButton(
            text=f"{'🟢' if c.enabled else '⚪'} {c.name}",
            callback_data=f"ch:open:{c.id}",
        )]
        for c in sorted(channels, key=lambda c: c.id)
    ]
    rows.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_card_menu(channel, settings) -> InlineKeyboardMarkup:
    """Карточка канala: вкл/выкл, настройки (значения на кнопках), источники, назад."""
    toggle = (
        InlineKeyboardButton(text="⏹ Выключить канал", callback_data=f"ch:toggle:{channel.id}")
        if channel.enabled
        else InlineKeyboardButton(text="▶️ Включить канал", callback_data=f"ch:toggle:{channel.id}")
    )
    maxposts = settings.max_posts_per_day if settings.max_posts_per_day is not None else "глоб."
    interval = settings.min_interval_minutes if settings.min_interval_minutes is not None else "глоб."
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [toggle],
            [
                InlineKeyboardButton(text=f"📈 Лимит/день: {maxposts}", callback_data=f"ch:set:{channel.id}:maxposts"),
                InlineKeyboardButton(text=f"⏱ Интервал: {interval}", callback_data=f"ch:set:{channel.id}:interval"),
            ],
            [InlineKeyboardButton(
                text=f"🔍 Фильтр новостей: {'вкл' if settings.filters_enabled else 'выкл'}",
                callback_data=f"ch:filter:{channel.id}",
            )],
            [InlineKeyboardButton(text="📰 Источники канала", callback_data=f"ch:sources:{channel.id}")],
            [InlineKeyboardButton(text="⬅️ К списку каналов", callback_data="ch:list")],
            _close_row(),
        ]
    )


def _close_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="✖️ Закрыть", callback_data="menu:close")]


def autoposting_menu(*, running: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="⏹ Остановить", callback_data="auto:stop")
        if running
        else InlineKeyboardButton(text="▶️ Запустить", callback_data="auto:run")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [toggle, InlineKeyboardButton(text="🔄 Статус", callback_data="auto:status")],
            [
                InlineKeyboardButton(text="📥 Очередь", callback_data="auto:queue"),
                InlineKeyboardButton(text="🚀 Опубликовать", callback_data="auto:publish"),
            ],
            _close_row(),
        ]
    )


def settings_menu(*, interval: int, freshness: int, maxposts: int, provider: str) -> InlineKeyboardMarkup:
    """Значения прямо на кнопках (стиль GRABBER «Автоподпись: нет»)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⏱ Интервал проверки: {interval} мин", callback_data="set:interval")],
            [InlineKeyboardButton(text=f"🕐 Окно свежести: {freshness} ч", callback_data="set:freshness")],
            [InlineKeyboardButton(text=f"📈 Лимит постов/день: {maxposts}", callback_data="set:maxposts")],
            [InlineKeyboardButton(text=f"🧠 LLM-провайдер: {provider}", callback_data="set:provider")],
            _close_row(),
        ]
    )


def provider_menu(providers: list[str], current: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=("✅ " if p == current else "") + p, callback_data=f"prov:{p}"
        )]
        for p in providers
    ]
    rows.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sources_menu(sources) -> InlineKeyboardMarkup:
    """Каждый источник — кнопка-переключатель (🟢/⚪), тап меняет вкл/выкл."""
    rows = [
        [InlineKeyboardButton(
            text=f"{'🟢' if src.enabled else '⚪'} [{src.id}] {src.type}: {src.name}",
            callback_data=f"src:toggle:{src.id}",
        )]
        for src in sorted(sources, key=lambda s: s.id)
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить источник", callback_data="src:add")])
    rows.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tools_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Уникализатор медиа", callback_data="tools:uniq")],
            [
                InlineKeyboardButton(text="🌿 VK Nature", callback_data="tools:nature"),
                InlineKeyboardButton(text="🎬 Shorts", callback_data="tools:shorts"),
            ],
            _close_row(),
        ]
    )


def process_menu(prefix: str) -> InlineKeyboardMarkup:
    """Подменю управления внешним процессом (nature/shorts): запуск/стоп/статус.
    prefix — 'nature' или 'shorts', callback_data = '<prefix>:run|stop|status'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Запустить", callback_data=f"{prefix}:run"),
                InlineKeyboardButton(text="⏹ Остановить", callback_data=f"{prefix}:stop"),
            ],
            [InlineKeyboardButton(text="🔄 Статус", callback_data=f"{prefix}:status")],
            _close_row(),
        ]
    )
