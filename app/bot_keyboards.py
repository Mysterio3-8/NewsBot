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

# Старые разделы (Создать пост/Автопостинг/Каналы/Источники/Настройки/Инструменты/
# Статус) убраны из главного меню по ТЗ 2026-07-19 — теперь всё под единым пультом
# «📦 Софты». Константы и их обработчики оставлены (доступ по /-командам + graceful
# для закешированной у пользователя клавиатуры), но кнопок в меню больше нет.
BTN_NEW_POST = "📝 Создать пост"
BTN_AUTOPOSTING = "🤖 Автопостинг"
BTN_CHANNELS = "📺 Каналы"
BTN_SOURCES = "📰 Источники"
BTN_SETTINGS = "⚙️ Настройки"
BTN_TOOLS = "🧰 Инструменты"
BTN_STATUS = "📊 Статус"
BTN_SOFTS = "📦 Софты"

MAIN_MENU_BUTTONS = (BTN_SOFTS,)


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню — единственная кнопка «📦 Софты» (ТЗ: центр управления всеми софтами)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SOFTS)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def softs_list_menu(rows) -> InlineKeyboardMarkup:
    """Список всех софтов — каждая кнопка открывает меню софта.
    rows — объекты с .soft_id/.title/.dot (view-модели из control_bot)."""
    buttons = [
        [InlineKeyboardButton(text=f"{r.dot} {r.title}", callback_data=f"soft:open:{r.soft_id}")]
        for r in rows
    ]
    buttons.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def soft_menu(
    soft_id: str,
    *,
    kind: str,
    running: bool,
    channel_id: int | None = None,
    soundcloud: bool = False,
    contract=None,
) -> InlineKeyboardMarkup:
    """Меню одного софта (набор кнопок из ТЗ). Для канала (kind='channel') кнопки
    Лимит/Интервал/Источники/Доп делегируют в готовые обработчики ch:*; для внешнего
    софта те же настройки идут через КОНТРАКТ (manager_contract.yaml в его каталоге) —
    значения показываются прямо на кнопках.

    soundcloud=True добавляет альбомный поток — софт объявляет его флагом в реестре."""
    power = (
        InlineKeyboardButton(text="⏹ Выключить", callback_data=f"soft:off:{soft_id}")
        if running
        else InlineKeyboardButton(text="▶️ Включить", callback_data=f"soft:on:{soft_id}")
    )
    rows = [[power, InlineKeyboardButton(text="📊 Статус", callback_data=f"soft:status:{soft_id}")]]
    if soundcloud:
        rows.append(
            [
                InlineKeyboardButton(text="🎵 Загрузить плейлист", callback_data=f"soft:sc:{soft_id}"),
                InlineKeyboardButton(text="📋 Очередь", callback_data=f"soft:scq:{soft_id}"),
            ]
        )
    if kind == "channel" and channel_id is not None:
        rows += [
            [
                InlineKeyboardButton(text="📅 Лимит в день", callback_data=f"ch:set:{channel_id}:maxposts"),
                InlineKeyboardButton(text="⏱ Интервал", callback_data=f"ch:set:{channel_id}:interval"),
            ],
            [
                InlineKeyboardButton(text="📚 Источники", callback_data=f"ch:sources:{channel_id}"),
                InlineKeyboardButton(text="📢 Каналы", callback_data=f"soft:dests:{channel_id}"),
            ],
            [InlineKeyboardButton(text="📝 Шаблоны текстов", callback_data="tpl:list")],
            [InlineKeyboardButton(text="⚙️ Дополнительные настройки", callback_data=f"ch:open:{channel_id}")],
        ]
    else:
        limit = _contract_value(contract, "max_posts_per_day")
        gap = _contract_interval(contract)
        quiet = _contract_quiet(contract)
        rows += [
            [
                InlineKeyboardButton(
                    text=f"📅 Лимит/день: {limit}", callback_data=f"soft:lim:{soft_id}:maxposts"
                ),
                InlineKeyboardButton(
                    text=f"⏱ Интервал: {gap}", callback_data=f"soft:lim:{soft_id}:interval"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🌙 Ночная пауза: {quiet}", callback_data=f"soft:lim:{soft_id}:quiet"
                ),
            ],
            [InlineKeyboardButton(text="📄 Показать контракт", callback_data=f"soft:cfg:{soft_id}")],
        ]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="soft:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
            # Видео-настройки канала (ТЗ 2026-07-28: «пусть также с помощью бота
            # управляется») — показываются только там, где видео-репост включён.
            *_video_rows(channel, settings),
            [InlineKeyboardButton(text="📰 Источники канала", callback_data=f"ch:sources:{channel.id}")],
            [InlineKeyboardButton(text="📝 Шаблоны текстов", callback_data="tpl:list")],
            [InlineKeyboardButton(text="⬅️ К списку каналов", callback_data="ch:list")],
            _close_row(),
        ]
    )


def _video_rows(channel, settings) -> list[list[InlineKeyboardButton]]:
    has_video = bool(settings.daily_video_youtube_channels) or settings.daily_video_group is not None
    if not has_video:
        return []
    return [[
        InlineKeyboardButton(
            text=f"🎬 Фильмов/день: {settings.daily_video_count}",
            callback_data=f"ch:set:{channel.id}:films",
        ),
        InlineKeyboardButton(
            text=f"✂️ Клипов на фильм: {settings.daily_clip_count}",
            callback_data=f"ch:set:{channel.id}:clips",
        ),
    ], [
        InlineKeyboardButton(
            text=f"⏳ Зазор фильмов: {settings.video_gap_minutes} мин",
            callback_data=f"ch:set:{channel.id}:filmgap",
        ),
    ]]


def _contract_value(contract, field: str) -> str:
    """Значение поля контракта для подписи кнопки. Не задано → «не задан»."""
    value = getattr(contract, field, None) if contract is not None else None
    return "не задан" if value is None else str(value)


def _contract_interval(contract) -> str:
    if contract is None or contract.min_interval_minutes is None:
        return "не задан"
    if contract.max_interval_minutes is not None:
        return f"{contract.min_interval_minutes}–{contract.max_interval_minutes}м"
    return f"{contract.min_interval_minutes}м"


def _contract_quiet(contract) -> str:
    if contract is None or contract.quiet_start_hour is None or contract.quiet_end_hour is None:
        return "выкл"
    return f"{contract.quiet_start_hour}–{contract.quiet_end_hour}ч"


def prompts_menu(rows, back_to: str) -> InlineKeyboardMarkup:
    """Список текстовых шаблонов. rows — объекты с .name/.title/.overridden.
    back_to — callback_data кнопки «Назад» (карточка софта, откуда пришли)."""
    buttons = [
        [InlineKeyboardButton(
            text=f"{'✏️' if r.overridden else '📄'} {r.title}",
            callback_data=f"tpl:open:{r.name}",
        )]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_to)])
    buttons.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def prompt_card_menu(name: str, *, overridden: bool) -> InlineKeyboardMarkup:
    """Карточка одного шаблона: изменить текст, сбросить к заводскому, назад."""
    rows = [[InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"tpl:edit:{name}")]]
    if overridden:
        rows.append(
            [InlineKeyboardButton(text="↩️ Сбросить к заводскому", callback_data=f"tpl:reset:{name}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 К шаблонам", callback_data="tpl:list")])
    rows.append(_close_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def settings_menu(
    *, interval: int, freshness: int, maxposts: int, provider: str, photo_design: bool
) -> InlineKeyboardMarkup:
    """Значения прямо на кнопках (стиль GRABBER «Автоподпись: нет»)."""
    design_state = "вкл 🟢" if photo_design else "выкл ⚪"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⏱ Интервал проверки: {interval} мин", callback_data="set:interval")],
            [InlineKeyboardButton(text=f"🕐 Окно свежести: {freshness} ч", callback_data="set:freshness")],
            [InlineKeyboardButton(text=f"📈 Лимит постов/день: {maxposts}", callback_data="set:maxposts")],
            [InlineKeyboardButton(text=f"🎨 Оформление фото: {design_state}", callback_data="set:photodesign")],
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
