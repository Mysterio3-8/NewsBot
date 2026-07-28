"""Telegram-бот управления софтом. Отдельный бот (свой токен CONTROL_BOT_TOKEN,
не путать с TG_BOT_TOKEN, которым публикуются посты в канал).

Команды: /run, /stop, /status, /publish, /queue, /provider — удалённое управление
сервисом. Доступ только у владельца (первый, кто нажал /start, либо CONTROL_BOT_OWNER_ID).

Логика команд вынесена в чистые функции (render_*/switch_provider/publish_now) — они
тестируются без aiogram; сам aiogram-слой (build_dispatcher/run_bot) — тонкая обвязка.
"""
from __future__ import annotations

import dataclasses
import logging
import os

from pathlib import Path

from app.config.loader import (
    CONFIG_PATH,
    AppConfig,
    ConfigValidationError,
    load_config,
    update_config_section,
    update_schedule_config,
)
from app.core.channel_settings import ChannelSettings
from app.core.maintenance.cleanup import cleanup_output, format_disk_report
from app.core.manual_post import MAX_BUTTONS, PostButton, parse_button_input
from app.core.media.uniquifier import MediaUniquifyError, uniquify_media
from app.core.publishing.footer import build_footer_links_from_config
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.core.scheduler import pick_next_post_to_publish
from app.core.testpost import test_post_now
from app.db.repository import Repository
from app.manager import systemd
from app.moscow_time import format_moscow_time
from app.factories import build_telegram_publisher, build_vk_publisher
from app.paths import OUTPUT_DIR
from app.process_controller import ProcessController

logger = logging.getLogger("app")

OWNER_SETTING_KEY = "control_bot_owner_id"
CONTROL_BOT_TOKEN_ENV = "CONTROL_BOT_TOKEN"
CONTROL_BOT_OWNER_ENV = "CONTROL_BOT_OWNER_ID"
NATURE_BOT_PATH_ENV = "NATURE_BOT_PATH"
SHORTS_PATH_ENV = "SHORTS_PATH"
SHORTS_BASE_URL_ENV = "SHORTS_BASE_URL"
SHORTS_DEFAULT_BASE_URL = "http://127.0.0.1:8080"
LLM_PROVIDERS = {"groq", "openrouter", "gemini", "ollama"}
UNIQUIFY_VARIANTS = 5
UNIQUIFY_INPUT_DIR = OUTPUT_DIR / "uniquify" / "input"
UNIQUIFY_OUTPUT_DIR = OUTPUT_DIR / "uniquify" / "output"
SHORTS_OUTPUT_DIR = OUTPUT_DIR / "shorts"

HELP_TEXT = (
    "Управление AI News Rewriter:\n"
    "/menu — открыть кнопочное меню (удобнее, чем команды)\n"
    "/newpost — собрать пост вручную: текст/медиа + URL-кнопки + уникализация "
    "текста → публикация в канал (аналог «Создать пост»)\n"
    "/run — запустить сервис\n"
    "/stop — остановить сервис\n"
    "/status — статус + последние публикации\n"
    "/publish — опубликовать лучший пост из очереди сейчас\n"
    "/testpost <ссылка на VK-пост> — взять конкретный пост по ссылке (в обход "
    "скоринга/фильтров — это ручной тест конкретного контента), прогнать через "
    "реальный рерайт+фото+вотермарк и опубликовать сразу, в обход дневного "
    "лимита/интервала (по запросу — тестовые посты всегда публикуются); "
    "повторно в ту же сеть один и тот же пост всё равно не уйдёт\n"
    "/queue — сколько постов в очереди\n"
    "/provider <groq|openrouter|gemini|ollama> — сменить LLM\n"
    "\nИсточники новостей:\n"
    "/sources — список источников (вкл/выкл)\n"
    "/source_on <id> — включить источник\n"
    "/source_off <id> — выключить источник\n"
    "/addsource <tg|vk> <url|group_id> <имя> — добавить источник\n"
    "\nТемп публикации:\n"
    "/settings — текущие настройки темпа\n"
    "/interval <мин> — как часто проверять каналы\n"
    "/freshness <часов> — окно свежести/бэклога\n"
    "/maxposts <n> — лимит постов в день\n"
    "/disk — занятое место и уборка временных файлов\n"
    "\nУправление VK Nature Bot (отдельный процесс, свой репозиторий):\n"
    "/nature_run — запустить\n"
    "/nature_stop — остановить\n"
    "/nature_status — статус + хвост лога\n"
    "\nНовостные шортсы через MoneyPrinter Turbo (отдельный процесс, свой репозиторий):\n"
    "/shorts_run — запустить сервис генерации\n"
    "/shorts_stop — остановить\n"
    "/shorts_status — статус + хвост лога\n"
    "/shorts <id поста> — сгенерировать короткое видео из рерайта поста и прислать "
    "сюда файлом (не публикует само — только присылает на проверку)\n"
    "\n📎 Пришли видео или фото файлом (до 20 МБ) — верну 5 уникальных версий "
    "без потери качества (для перезаливов)."
)

# Приветствие на /start (стиль GRABBER, запрос пользователя 2026-07-08) — вместо
# простыни команд. Полный список команд остаётся в /help. Название/буллеты легко
# поменять здесь.
WELCOME_TEXT = (
    "❤️ Автопостер — автоматизация контента и уникальный постинг с нейросетью\n\n"
    "🗳 Автогенерация уникального контента\n"
    "🔍 Фильтры контента\n"
    "🍑 Сбор данных из ваших источников\n"
    "⚙️ Индивидуальные настройки\n"
    "➕ Автопостинг\n\n"
    "Выбирай раздел в меню снизу 👇  (полный список команд — /help)"
)


def build_nature_controller(env: dict | None = None) -> ProcessController | None:
    """None, если NATURE_BOT_PATH не задан в .env — команды /nature_* тогда недоступны.
    Отдельный процесс/venv (см. CLAUDE.md: код НЕ сливаем с vk-nature-bot)."""
    source = env if env is not None else os.environ
    raw_path = source.get(NATURE_BOT_PATH_ENV)
    if not raw_path:
        return None
    bot_dir = Path(raw_path)
    python_path = bot_dir / "venv" / "Scripts" / "python.exe"
    return ProcessController(
        name="nature",
        command=[str(python_path), "-m", "app.main"],
        cwd=bot_dir,
        log_path=OUTPUT_DIR / "nature_bot.log",
    )


def render_nature_status(controller: ProcessController | None) -> str:
    if controller is None:
        return f"VK Nature Bot не настроен — задай {NATURE_BOT_PATH_ENV} в .env."
    running = controller.is_running()
    lines = [f"VK Nature Bot: {'🟢 запущен' if running else '🔴 остановлен'}"]
    started = format_moscow_time(controller.started_at)
    if started:
        lines.append(f"Старт: {started} (МСК)")
    lines.append("\nХвост лога:\n" + controller.tail_log(10))
    return "\n".join(lines)


def build_shorts_controller(env: dict | None = None) -> ProcessController | None:
    """None, если SHORTS_PATH не задан в .env — команды /shorts_* тогда недоступны.
    MoneyPrinter Turbo (Shorts) — отдельный проект/venv, общаемся только по HTTP
    (app/core/shorts/client.py), код не сливаем."""
    source = env if env is not None else os.environ
    raw_path = source.get(SHORTS_PATH_ENV)
    if not raw_path:
        return None
    shorts_dir = Path(raw_path)
    python_path = shorts_dir / "venv" / "Scripts" / "python.exe"
    return ProcessController(
        name="shorts",
        command=[str(python_path), "main.py"],
        cwd=shorts_dir,
        log_path=OUTPUT_DIR / "shorts_service.log",
    )


def render_shorts_status(controller: ProcessController | None) -> str:
    if controller is None:
        return f"MoneyPrinter Shorts не настроен — задай {SHORTS_PATH_ENV} в .env."
    running = controller.is_running()
    lines = [f"MoneyPrinter Shorts: {'🟢 запущен' if running else '🔴 остановлен'}"]
    started = format_moscow_time(controller.started_at)
    if started:
        lines.append(f"Старт: {started} (МСК)")
    lines.append("\nХвост лога:\n" + controller.tail_log(10))
    return "\n".join(lines)


# --- Единый пульт «📦 Софты» -------------------------------------------------
# ТЗ 2026-07-19: центр управления всеми софтами из одного бота. «Софт» —
# гетерогенная сущность: движок новостей (ServiceController, asyncio-задача в этом
# процессе), каждый новостной КАНАЛ (строка в БД, полное управление уже есть в
# ch:*-обработчиках) и каждый ВНЕШНИЙ процесс (ProcessController, чужой репо/venv).
# Реестр строится автоматически: добавил канал в БД или внешний путь в .env — софт
# появился в списке без правок кода.

SOFT_ENGINE_ID = "engine"
SOFT_KIND_ENGINE = "engine"
SOFT_KIND_CHANNEL = "channel"
SOFT_KIND_PROCESS = "process"


@dataclasses.dataclass(frozen=True)
class Soft:
    """Идентичность софта для навигации. Действия (вкл/выкл/статус) резолвятся в
    build_dispatcher по kind + soft_id, чтобы не тащить контроллеры в чистый слой."""

    soft_id: str
    title: str
    kind: str
    channel_id: int | None = None


def build_soft_list(channels, process_entries: list[tuple[str, str]]) -> list[Soft]:
    """channels — список каналов из repo.list_channels(); process_entries — пары
    (soft_id, title) внешних софтов из реестра менеджера. Движок всегда первый."""
    softs = [Soft(SOFT_ENGINE_ID, "📰 Движок новостей", SOFT_KIND_ENGINE)]
    for ch in sorted(channels, key=lambda c: c.id):
        softs.append(Soft(f"ch_{ch.id}", f"📺 {ch.name}", SOFT_KIND_CHANNEL, ch.id))
    for soft_id, title in process_entries:
        softs.append(Soft(soft_id, title, SOFT_KIND_PROCESS))
    return softs


def find_soft(softs: list[Soft], soft_id: str) -> Soft | None:
    return next((s for s in softs if s.soft_id == soft_id), None)


def render_soft_list(softs: list[Soft], statuses: dict[str, str]) -> str:
    lines = ["📦 Софты — выбери софт кнопкой ниже:\n"]
    for s in softs:
        lines.append(f"{statuses.get(s.soft_id, '❔')} {s.title}")
    return "\n".join(lines)


def soft_list_rows(softs: list[Soft], statuses: dict[str, str]) -> list:
    """View-модели для клавиатуры списка софтов."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(soft_id=s.soft_id, title=s.title, dot=statuses.get(s.soft_id, "❔"))
        for s in softs
    ]


async def generate_short_for_post(repo: Repository, post_id: int, base_url: str) -> Path | str:
    """Генерирует короткое видео из рерайта поста через Shorts API. Возвращает путь
    к скачанному видео либо строку с описанием ошибки (для ответа пользователю)."""
    post = repo.get_processed_post(post_id)
    if post is None:
        return f"Пост id={post_id} не найден."
    if not post.rewritten_text:
        return f"У поста id={post_id} нет текста рерайта."

    import asyncio

    from app.core.shorts import client as shorts_client

    def _run() -> Path:
        task_id = shorts_client.create_task(base_url, post.headline or "Новость", post.rewritten_text)
        videos = shorts_client.wait_for_video(base_url, task_id)
        destination = SHORTS_OUTPUT_DIR / f"{post_id}_{task_id}.mp4"
        shorts_client.download_video(videos[0], destination)
        return destination

    try:
        return await asyncio.to_thread(_run)
    except shorts_client.ShortsClientError as error:
        return f"Не получилось сгенерировать шортс: {error}"
    except Exception as error:  # сервис недоступен/сеть — не роняем бот
        logger.exception("Генерация шортса упала")
        return f"Ошибка: {error}"


def uniquify_media_file(input_path: Path, output_dir: Path = UNIQUIFY_OUTPUT_DIR) -> list[Path]:
    """Обёртка над uniquify_media с дефолтным числом вариантов — для бота и тестов."""
    return uniquify_media(input_path, count=UNIQUIFY_VARIANTS, output_dir=output_dir)


def get_owner_id(repo: Repository) -> int | None:
    env_owner = os.environ.get(CONTROL_BOT_OWNER_ENV)
    if env_owner:
        return int(env_owner)
    stored = repo.get_setting(OWNER_SETTING_KEY)
    return int(stored) if stored else None


def register_owner(repo: Repository, user_id: int) -> None:
    repo.set_setting(OWNER_SETTING_KEY, str(user_id))


CONTROL_BOT_EXTRA_IDS_ENV = "CONTROL_BOT_EXTRA_OWNER_IDS"


def _extra_authorized_ids() -> set[int]:
    """Доп. авторизованные пользователи (напр. напарник) — список Telegram user_id через
    запятую в CONTROL_BOT_EXTRA_OWNER_IDS. Помимо основного владельца из get_owner_id."""
    raw = os.environ.get(CONTROL_BOT_EXTRA_IDS_ENV, "")
    return {int(part) for part in raw.replace(" ", "").split(",") if part}


def is_authorized(repo: Repository, user_id: int) -> bool:
    owner = get_owner_id(repo)
    if owner is not None and owner == user_id:
        return True
    return user_id in _extra_authorized_ids()


def handle_start(repo: Repository, user_id: int) -> str:
    """Первый /start без настроенного владельца — регистрирует отправителя владельцем.
    Показывает приветствие (WELCOME_TEXT), не простыню команд — те остались в /help."""
    if get_owner_id(repo) is None:
        register_owner(repo, user_id)
        return "Вы зарегистрированы как владелец бота.\n\n" + WELCOME_TEXT
    if is_authorized(repo, user_id):
        return WELCOME_TEXT
    return "Доступ запрещён: бот уже привязан к другому владельцу."


def render_status(controller, repo: Repository) -> str:
    running = controller.is_running()
    lines = [f"Сервис: {'🟢 запущен' if running else '🔴 остановлен'}"]
    started = format_moscow_time(controller.started_at)
    if started:
        lines.append(f"Старт: {started} (МСК)")

    recent = repo.list_recent_published(limit=5)
    if recent:
        lines.append("\nПоследние публикации (МСК):")
        for post in recent:
            when = format_moscow_time(post.published_at) or "?"
            lines.append(f"• {when} — {post.headline or '(без заголовка)'}")
    return "\n".join(lines)


def render_queue(repo: Repository) -> str:
    queued = repo.list_processed_posts(status="queued")
    with_image = sum(1 for post in queued if post.image_paths)
    return f"В очереди: {len(queued)} постов ({with_image} с картинкой)"


def switch_provider(config_path, provider: str) -> str:
    provider = provider.strip().lower()
    if provider not in LLM_PROVIDERS:
        return f"Неизвестный провайдер «{provider}». Доступно: {', '.join(sorted(LLM_PROVIDERS))}"
    update_config_section(config_path, "llm", provider=provider)
    return f"LLM-провайдер → {provider}. Перезапусти сервис (/stop, /run), чтобы применить."


def render_sources(repo: Repository) -> str:
    """Список источников с id, типом, именем и статусом вкл/выкл — для /sources."""
    sources = repo.list_sources()
    if not sources:
        return "Источников нет. Добавь: /addsource tg https://t.me/канал Имя"
    lines = ["Источники (🟢 вкл / ⚪ выкл):"]
    for src in sorted(sources, key=lambda s: s.id):
        mark = "🟢" if src.enabled else "⚪"
        lines.append(f"{mark} [{src.id}] {src.type}: {src.name} — {src.url}")
    lines.append("\n/source_on <id> · /source_off <id> · /addsource <tg|vk> <url> <имя>")
    return "\n".join(lines)


def toggle_source(repo: Repository, arg: str, *, enabled: bool) -> str:
    """Включить/выключить источник по id. Меняется в БД — эффект сразу, без рестарта."""
    arg = arg.strip()
    if not arg.isdigit():
        return "Укажи числовой id источника: /source_off 7 (см. /sources)"
    source = repo.get_source(int(arg))
    if source is None:
        return f"Источник {arg} не найден (см. /sources)."
    repo.update_source(source.id, enabled=enabled)
    state = "включён 🟢" if enabled else "выключен ⚪"
    return f"Источник [{source.id}] {source.name} {state}."


def add_source(repo: Repository, arg: str) -> str:
    """Добавить источник: /addsource <tg|vk> <url|group_id> <имя>. Выключен по умолчанию —
    чтобы включить осознанно через /source_on после проверки."""
    parts = arg.split(maxsplit=2)
    if len(parts) < 3:
        return "Формат: /addsource <tg|vk> <url или group_id> <имя>"
    src_type, url, name = parts[0].strip().lower(), parts[1].strip(), parts[2].strip()
    if src_type not in ("tg", "vk"):
        return "Тип должен быть tg или vk."
    source = repo.create_source(type=src_type, name=name, url=url)
    repo.update_source(source.id, enabled=False)
    return f"Источник [{source.id}] {name} добавлен (выключен). Включить: /source_on {source.id}"


def render_channels(repo: Repository) -> str:
    """Список каналов с их статусом — для меню «Каналы» (мультиканальность)."""
    channels = repo.list_channels()
    if not channels:
        return "Каналов нет."
    lines = ["📺 Каналы (🟢 вкл / ⚪ выкл):\n"]
    for c in channels:
        mark = "🟢" if c.enabled else "⚪"
        lines.append(f"{mark} {c.name}")
    lines.append("\nВыбери канал кнопкой ниже, чтобы настроить.")
    return "\n".join(lines)


def render_channel_card(repo: Repository, channel_id: int) -> str:
    """Карточка канала: таргеты, настройки, число источников."""
    channel = repo.get_channel(channel_id)
    if channel is None:
        return "Канал не найден."
    settings = ChannelSettings.from_json(channel.settings_json)
    n_src = len(repo.list_sources(channel_id=channel_id))
    return (
        f"📺 {channel.name}\n\n"
        f"Статус: {'🟢 включён' if channel.enabled else '⚪ выключен'}\n"
        f"VK: {channel.vk_destination or '—'}\n"
        f"TG: {channel.tg_destination or '—'}\n"
        f"Лимит/день: {settings.max_posts_per_day if settings.max_posts_per_day is not None else 'глобальный'}\n"
        f"Интервал: {settings.min_interval_minutes if settings.min_interval_minutes is not None else 'глобальный'} мин\n"
        f"Фильтр новостей: {'вкл' if settings.filters_enabled else 'выкл (лить всё)'}\n"
        f"Источников: {n_src}"
    )


def is_photo_design_on(repo: Repository, config: AppConfig) -> bool:
    """Текущее состояние оформления фото: настройка из бота (photo_design_enabled)
    перекрывает дефолт config.headline_card.enabled. Ключ совпадает с
    check_cycle.PHOTO_DESIGN_SETTING."""
    raw = repo.get_setting("photo_design_enabled")
    if raw is None:
        return config.headline_card.enabled
    return raw == "1"


def toggle_photo_design(repo: Repository, config: AppConfig) -> str:
    """Тумблер оформления фото (зелёный fade + лого + заголовок) — запрос пользователя
    2026-07-11 «сделать чтобы можно было включать/выключать в боте»."""
    new_state = not is_photo_design_on(repo, config)
    repo.set_setting("photo_design_enabled", "1" if new_state else "0")
    return f"Оформление фото (fade+лого+заголовок): {'вкл 🟢' if new_state else 'выкл ⚪'}"


def toggle_channel(repo: Repository, channel_id: int) -> str:
    """Включить/выключить канал. Выключенный не публикует (cycle_job его пропускает)."""
    channel = repo.get_channel(channel_id)
    if channel is None:
        return "Канал не найден."
    new_state = not channel.enabled
    repo.update_channel(channel_id, enabled=new_state)
    return f"Канал «{channel.name}»: {'включён 🟢' if new_state else 'выключен ⚪'}"


def toggle_channel_filter(repo: Repository, channel_id: int) -> str:
    """Переключить новостной фильтр канала (вкл = фильтруем как новости, выкл = лить всё)."""
    channel = repo.get_channel(channel_id)
    if channel is None:
        return "Канал не найден."
    settings = ChannelSettings.from_json(channel.settings_json)
    settings = dataclasses.replace(settings, filters_enabled=not settings.filters_enabled)
    repo.update_channel(channel_id, settings_json=settings.to_json())
    return f"Фильтр канала «{channel.name}»: {'вкл' if settings.filters_enabled else 'выкл (лить всё)'}"


def set_channel_setting(repo: Repository, channel_id: int, field: str, value_str: str) -> str:
    """Изменить числовую настройку канала (maxposts/interval) из бота."""
    value_str = value_str.strip()
    if not value_str.isdigit():
        return "Нужно число. Попробуй ещё раз."
    value = int(value_str)
    channel = repo.get_channel(channel_id)
    if channel is None:
        return "Канал не найден."
    settings = ChannelSettings.from_json(channel.settings_json)
    if field == "maxposts":
        settings = dataclasses.replace(settings, max_posts_per_day=value)
        label = "лимит/день"
    elif field == "interval":
        settings = dataclasses.replace(settings, min_interval_minutes=value)
        label = "интервал (мин)"
    elif field == "films":
        settings = dataclasses.replace(settings, daily_video_count=value)
        label = "фильмов/день"
    elif field == "clips":
        settings = dataclasses.replace(settings, daily_clip_count=value)
        label = "клипов на фильм"
    else:
        return "Неизвестная настройка."
    repo.update_channel(channel_id, settings_json=settings.to_json())
    return f"Канал «{channel.name}»: {label} = {value} ✅"


def render_disk() -> str:
    """Сводка по временным файлам + ручная уборка. Добавлено после инцидента
    2026-07-28: диск дошёл до 94% и публикация встала, а увидеть это можно было
    только по ssh."""
    freed = cleanup_output(OUTPUT_DIR)
    report = format_disk_report(OUTPUT_DIR)
    return (
        f"💾 Временные файлы:\n{report}\n\n"
        f"Уборка: удалено {freed.removed_files}, освобождено {freed.freed_mb:.0f} МБ"
    )


def render_settings(config: AppConfig) -> str:
    """Текущие настройки темпа публикации — для /settings."""
    schedule = config.publishing.schedule
    return (
        "⚙️ Настройки темпа:\n"
        f"• Проверка каналов: каждые {config.monitoring.check_interval_minutes} мин "
        f"(±{schedule.jitter_minutes})\n"
        f"• Окно свежести/бэклога: {schedule.publish_freshness_hours} ч\n"
        f"• Мин. интервал между постами: {schedule.min_interval_minutes} мин\n"
        f"• Лимит постов в день: {schedule.max_posts_per_day}\n"
        "\nИзменить: /interval <мин> · /freshness <часов> · /maxposts <n>\n"
        "(после изменения — /stop, /run, чтобы применить)"
    )


def set_check_interval(config_path, arg: str) -> str:
    if not arg.strip().isdigit():
        return "Укажи число минут: /interval 20"
    update_config_section(config_path, "monitoring", check_interval_minutes=int(arg.strip()))
    return f"Проверка каналов → каждые {arg.strip()} мин. Перезапусти сервис (/stop, /run)."


def set_schedule_number(config_path, arg: str, *, field: str, label: str) -> str:
    if not arg.strip().isdigit():
        return f"Укажи число: /{field.split('_')[0]} <n>"
    try:
        update_schedule_config(config_path, **{field: int(arg.strip())})
    except ConfigValidationError as error:
        return f"Не сохранено: {error}"
    return f"{label} → {arg.strip()}. Перезапусти сервис (/stop, /run)."


async def publish_now(repo: Repository, config: AppConfig) -> str:
    post = pick_next_post_to_publish(
        repo,
        max_posts_per_day=config.publishing.schedule.max_posts_per_day,
        freshness_hours=config.publishing.schedule.publish_freshness_hours,
    )
    if post is None:
        return "Нет постов в очереди для публикации."

    footer_links = build_footer_links_from_config(config.footer)
    schedule = config.publishing.schedule
    results: list[str] = []

    tg_publisher = build_telegram_publisher(config)
    if tg_publisher is not None and config.publishing.telegram.enabled:
        result = await publish_queued_post(
            repo, tg_publisher, post_id=post.id,
            chat_id=config.publishing.telegram.destination,
            footer_links=footer_links,
            max_posts_per_day=schedule.max_posts_per_day,
            min_interval_minutes=schedule.min_interval_minutes,
            include_hashtags=config.rewrite.include_hashtags,
        )
        results.append("TG: ✅" if result.success else f"TG: ❌ {result.error}")

    vk_publisher = build_vk_publisher(config)
    if vk_publisher is not None and config.publishing.vk.enabled:
        result = publish_queued_post_vk(
            repo, vk_publisher, post_id=post.id,
            group_id=int(config.publishing.vk.destination),
            footer_links=footer_links,
            max_posts_per_day=schedule.max_posts_per_day,
            min_interval_minutes=schedule.min_interval_minutes,
            include_hashtags=config.rewrite.include_hashtags,
        )
        results.append("VK: ✅" if result.success else f"VK: ❌ {result.error}")

    if not results:
        return "Публикаторы не настроены (нет токенов в .env)."
    return f"Пост «{post.headline}» (id={post.id}):\n" + "\n".join(results)


def build_dispatcher(
    controller,
    repo: Repository,
    config_path=CONFIG_PATH,
    nature_controller=None,
    shorts_controller=None,
    shorts_base_url: str = SHORTS_DEFAULT_BASE_URL,
    manager_repo=None,
):
    """aiogram-обвязка. Импорт aiogram здесь, чтобы чистые функции выше тестировались
    без установленного aiogram-раннера."""
    from aiogram import F, Dispatcher
    from aiogram.filters import Command
    from aiogram.types import Message

    dp = Dispatcher()

    async def guard(message: Message) -> bool:
        if is_authorized(repo, message.from_user.id):
            return True
        await message.answer("Доступ запрещён.")
        return False

    @dp.message(Command("start"))
    async def on_start(message: Message) -> None:
        text = handle_start(repo, message.from_user.id)
        markup = kb.main_menu() if is_authorized(repo, message.from_user.id) else None
        await message.answer(text, reply_markup=markup)

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        if await guard(message):
            await message.answer(HELP_TEXT)

    @dp.message(Command("run"))
    async def on_run(message: Message) -> None:
        if await guard(message):
            started = controller.start()
            await message.answer("🟢 Сервис запущен." if started else "Сервис уже запущен.")

    @dp.message(Command("stop"))
    async def on_stop(message: Message) -> None:
        if await guard(message):
            stopped = controller.stop()
            await message.answer("🔴 Сервис остановлен." if stopped else "Сервис уже остановлен.")

    @dp.message(Command("status"))
    async def on_status(message: Message) -> None:
        if await guard(message):
            await message.answer(render_status(controller, repo))

    @dp.message(Command("queue"))
    async def on_queue(message: Message) -> None:
        if await guard(message):
            await message.answer(render_queue(repo))

    @dp.message(Command("disk"))
    async def on_disk(message: Message) -> None:
        if await guard(message):
            await message.answer(render_disk())

    @dp.message(Command("publish"))
    async def on_publish(message: Message) -> None:
        if await guard(message):
            await message.answer("Публикую…")
            await message.answer(await publish_now(repo, load_config(config_path)))

    @dp.message(Command("testpost"))
    async def on_testpost(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer(
                "Укажи ссылку: /testpost https://vk.com/wall-152992737_8999245"
            )
            return
        await message.answer("Готовлю тестовый пост… может занять минуту.")
        from app.core.llm.client import LLMClient

        config = load_config(config_path)
        llm_client = LLMClient(config.llm)
        result = await test_post_now(repo, config, llm_client, vk_ref=parts[1].strip())
        await message.answer(result)

    @dp.message(Command("provider"))
    async def on_provider(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи провайдера: /provider groq")
            return
        await message.answer(switch_provider(config_path, parts[1]))

    @dp.message(Command("sources"))
    async def on_sources(message: Message) -> None:
        if await guard(message):
            await message.answer(render_sources(repo))

    @dp.message(Command("source_on"))
    async def on_source_on(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(toggle_source(repo, parts[1] if len(parts) > 1 else "", enabled=True))

    @dp.message(Command("source_off"))
    async def on_source_off(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(toggle_source(repo, parts[1] if len(parts) > 1 else "", enabled=False))

    @dp.message(Command("addsource"))
    async def on_addsource(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(add_source(repo, parts[1] if len(parts) > 1 else ""))

    @dp.message(Command("settings"))
    async def on_settings(message: Message) -> None:
        if await guard(message):
            await message.answer(render_settings(load_config(config_path)))

    @dp.message(Command("interval"))
    async def on_interval(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(set_check_interval(config_path, parts[1] if len(parts) > 1 else ""))

    @dp.message(Command("freshness"))
    async def on_freshness(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(
            set_schedule_number(
                config_path, parts[1] if len(parts) > 1 else "",
                field="publish_freshness_hours", label="Окно свежести (часов)",
            )
        )

    @dp.message(Command("maxposts"))
    async def on_maxposts(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        await message.answer(
            set_schedule_number(
                config_path, parts[1] if len(parts) > 1 else "",
                field="max_posts_per_day", label="Лимит постов в день",
            )
        )

    @dp.message(Command("nature_run"))
    async def on_nature_run(message: Message) -> None:
        if not await guard(message):
            return
        if nature_controller is None:
            await message.answer(render_nature_status(None))
            return
        started = nature_controller.start()
        await message.answer("🟢 VK Nature Bot запущен." if started else "Уже запущен.")

    @dp.message(Command("nature_stop"))
    async def on_nature_stop(message: Message) -> None:
        if not await guard(message):
            return
        if nature_controller is None:
            await message.answer(render_nature_status(None))
            return
        stopped = nature_controller.stop()
        await message.answer("🔴 VK Nature Bot остановлен." if stopped else "Уже остановлен.")

    @dp.message(Command("nature_status"))
    async def on_nature_status(message: Message) -> None:
        if await guard(message):
            await message.answer(render_nature_status(nature_controller))

    @dp.message(Command("shorts_run"))
    async def on_shorts_run(message: Message) -> None:
        if not await guard(message):
            return
        if shorts_controller is None:
            await message.answer(render_shorts_status(None))
            return
        started = shorts_controller.start()
        await message.answer("🟢 MoneyPrinter Shorts запущен." if started else "Уже запущен.")

    @dp.message(Command("shorts_stop"))
    async def on_shorts_stop(message: Message) -> None:
        if not await guard(message):
            return
        if shorts_controller is None:
            await message.answer(render_shorts_status(None))
            return
        stopped = shorts_controller.stop()
        await message.answer("🔴 MoneyPrinter Shorts остановлен." if stopped else "Уже остановлен.")

    @dp.message(Command("shorts_status"))
    async def on_shorts_status(message: Message) -> None:
        if await guard(message):
            await message.answer(render_shorts_status(shorts_controller))

    @dp.message(Command("shorts"))
    async def on_shorts(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Укажи id поста: /shorts 39")
            return
        if shorts_controller is None or not shorts_controller.is_running():
            await message.answer("MoneyPrinter Shorts не запущен — сначала /shorts_run.")
            return

        from aiogram.types import FSInputFile

        post_id = int(parts[1].strip())
        await message.answer("Генерирую шортс… может занять несколько минут.")
        result = await generate_short_for_post(repo, post_id, shorts_base_url)
        if isinstance(result, str):
            await message.answer(result)
            return
        await message.answer_video(FSInputFile(result), caption="Черновик шортса — на проверку, не опубликован")

    # --- Кнопочное меню (стиль GRABBER) + FSM ---
    # Reply-меню снизу открывает инлайн-подменю; часть команд остаётся для совместимости.
    # Регистрируется ДО общего медиа-хендлера (уникализатора) ниже: пока владелец в
    # шагах конструктора/настройки, его текст/медиа ловят state-хендлеры, а не уникализатор.
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.filters import StateFilter
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

    from app import bot_keyboards as kb

    # Внешние софты берутся из БД-реестра менеджера (manager_repo). Готовые
    # контроллеры процессов (Природа/Shorts из .env) переиспользуются по soft_id —
    # чтобы один и тот же проект не запускался дважды. Остальные софты реестра пока
    # без управления (нужен путь/systemd-юнит на VPS — следующий срез).
    _proc_controllers: dict[str, object] = {}
    if nature_controller is not None:
        _proc_controllers["p_nature"] = nature_controller
    if shorts_controller is not None:
        _proc_controllers["p_shorts"] = shorts_controller

    def _process_softs() -> list[tuple[str, str]]:
        if manager_repo is None:
            return []
        return [(r.soft_id, r.title) for r in manager_repo.list_softs() if r.kind == "process"]

    def _softs() -> list[Soft]:
        return build_soft_list(repo.list_channels(), _process_softs())

    def _soft_units(soft_id: str) -> list[str]:
        """systemd-юниты софта из реестра (софт = набор юнитов, см. manager/systemd.py)."""
        record = manager_repo.get_soft(soft_id) if manager_repo else None
        return systemd.parse_units(record.systemd_units_json) if record else []

    def _soft_statuses() -> dict[str, str]:
        statuses = {SOFT_ENGINE_ID: "🟢" if controller.is_running() else "🔴"}
        for ch in repo.list_channels():
            statuses[f"ch_{ch.id}"] = "🟢" if ch.enabled else "⚪"
        for soft_id, _title in _process_softs():
            units = _soft_units(soft_id)
            if units:
                statuses[soft_id] = "🟢" if systemd.is_active(units) else "🔴"
                continue
            ctrl = _proc_controllers.get(soft_id)
            statuses[soft_id] = ("🟢" if ctrl.is_running() else "🔴") if ctrl is not None else "▫️"
        return statuses

    def _soft_running(soft: Soft) -> bool:
        if soft.kind == SOFT_KIND_ENGINE:
            return controller.is_running()
        if soft.kind == SOFT_KIND_CHANNEL:
            ch = repo.get_channel(soft.channel_id)
            return bool(ch and ch.enabled)
        units = _soft_units(soft.soft_id)
        if units:
            return systemd.is_active(units)
        ctrl = _proc_controllers.get(soft.soft_id)
        return bool(ctrl and ctrl.is_running())

    def _set_soft_running(soft: Soft, on: bool) -> bool:
        """True — управление есть и применено; False — для софта оно ещё не настроено."""
        if soft.kind == SOFT_KIND_ENGINE:
            controller.start() if on else controller.stop()
            return True
        if soft.kind == SOFT_KIND_CHANNEL:
            repo.update_channel(soft.channel_id, enabled=on)
            return True
        units = _soft_units(soft.soft_id)
        if units:
            return systemd.start(units) if on else systemd.stop(units)
        ctrl = _proc_controllers.get(soft.soft_id)
        if ctrl is None:
            return False
        ctrl.start() if on else ctrl.stop()
        return True

    def _soft_status_text(soft: Soft) -> str:
        if soft.kind == SOFT_KIND_ENGINE:
            return render_status(controller, repo)
        if soft.kind == SOFT_KIND_CHANNEL:
            return render_channel_card(repo, soft.channel_id)
        units = _soft_units(soft.soft_id)
        if units:
            return f"{soft.title}\n\n{systemd.status_text(units)}"
        if soft.soft_id == "p_nature":
            return render_nature_status(nature_controller)
        if soft.soft_id == "p_shorts":
            return render_shorts_status(shorts_controller)
        record = manager_repo.get_soft(soft.soft_id) if manager_repo else None
        host = record.host if record else "?"
        return (
            f"{soft.title}\n\nВ реестре менеджера (хост: {host}). systemd-юниты не заданы — "
            f"управление отсюда недоступно."
        )

    class PostCreation(StatesGroup):
        waiting_content = State()
        configuring = State()
        waiting_button = State()

    class SettingInput(StatesGroup):
        waiting_value = State()  # ждём число для выбранной настройки (интервал/свежесть/лимит)

    class AddSourceInput(StatesGroup):
        waiting = State()  # ждём "tg|vk url имя"

    class ChannelSettingInput(StatesGroup):
        waiting_value = State()  # ждём число для настройки канала (лимит/интервал)

    # Единое меню-сообщение на чат: навигация РЕДАКТИРУЕТ его, а не плодит новые
    # (запрос пользователя 2026-07-08). chat_id -> message_id последнего меню.
    menu_messages: dict[int, int] = {}

    def _load_settings_menu() -> InlineKeyboardMarkup:
        config = load_config(config_path)
        return kb.settings_menu(
            interval=config.monitoring.check_interval_minutes,
            freshness=config.publishing.schedule.publish_freshness_hours,
            maxposts=config.publishing.schedule.max_posts_per_day,
            provider=config.llm.provider,
            photo_design=is_photo_design_on(repo, config),
        )

    def _autoposting_text() -> str:
        state = "🟢 сервис запущен" if controller.is_running() else "🔴 сервис остановлен"
        return f"🤖 Автопостинг новостей\n\n{state}"

    async def _show_section(message: Message, text: str, markup) -> None:
        """Показать раздел в ЕДИНОМ меню-сообщении чата: редактируем прошлое меню,
        если оно есть, иначе шлём новое и запоминаем id. Так тап по нижнему меню не
        плодит сообщения, а обновляет одно."""
        chat_id = message.chat.id
        msg_id = menu_messages.get(chat_id)
        if msg_id is not None:
            try:
                await message.bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id, reply_markup=markup
                )
                return
            except TelegramBadRequest:
                pass  # меню удалили/устарело — отправим новое ниже
        sent = await message.answer(text, reply_markup=markup)
        menu_messages[chat_id] = sent.message_id

    async def _edit_current(cb: CallbackQuery, text: str, markup) -> None:
        """Редактировать текущее меню-сообщение по нажатию инлайн-кнопки (без нового)."""
        menu_messages[cb.message.chat.id] = cb.message.message_id
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            try:
                await cb.message.edit_reply_markup(reply_markup=markup)
            except TelegramBadRequest:
                pass

    async def _edit_menu_message(message: Message, text: str, markup) -> None:
        """После текстового ввода (число/источник) обновить меню-сообщение чата вместо
        нового ответа. Если меню потеряно — отправит новое."""
        chat_id = message.chat.id
        msg_id = menu_messages.get(chat_id)
        if msg_id is not None:
            try:
                await message.bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id, reply_markup=markup
                )
                return
            except TelegramBadRequest:
                pass
        sent = await message.answer(text, reply_markup=markup)
        menu_messages[chat_id] = sent.message_id

    def _configure_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔀 Уникализировать текст", callback_data="mp:uniq")],
                [InlineKeyboardButton(text="➕ Добавить кнопку", callback_data="mp:addbtn")],
                [
                    InlineKeyboardButton(text="👁 Превью", callback_data="mp:preview"),
                    InlineKeyboardButton(text="✅ Опубликовать", callback_data="mp:publish"),
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="mp:cancel")],
            ]
        )

    async def _callback_guard(cb: CallbackQuery) -> bool:
        if is_authorized(repo, cb.from_user.id):
            return True
        await cb.answer("Доступ запрещён.", show_alert=True)
        return False

    @dp.message(Command("newpost"))
    async def on_newpost(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.set_state(PostCreation.waiting_content)
        await message.answer("📝 Пришли пост: текст (можно с фото или видео одним сообщением).")

    @dp.message(Command("cancel"))
    async def on_cancel(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await message.answer("Отменено.")

    @dp.message(Command("menu"))
    async def on_menu(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await message.answer("Меню открыто 👇", reply_markup=kb.main_menu())

    # --- Нажатия главного reply-меню. Регистрируются ДО state-хендлеров: тап по меню
    # прерывает любой текущий шаг (state.clear) и переключает раздел. ---
    @dp.message(F.text == kb.BTN_NEW_POST)
    async def on_menu_newpost(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.set_state(PostCreation.waiting_content)
        await message.answer("📝 Пришли пост: текст (можно с фото или видео одним сообщением).")

    @dp.message(F.text == kb.BTN_AUTOPOSTING)
    async def on_menu_autoposting(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(
            message, _autoposting_text(), kb.autoposting_menu(running=controller.is_running())
        )

    @dp.message(F.text == kb.BTN_SOURCES)
    async def on_menu_sources(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(message, "📰 Источники (тап — вкл/выкл):", kb.sources_menu(repo.list_sources()))

    @dp.message(F.text == kb.BTN_CHANNELS)
    async def on_menu_channels(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(message, render_channels(repo), kb.channels_menu(repo.list_channels()))

    @dp.message(F.text == kb.BTN_SETTINGS)
    async def on_menu_settings(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(message, "⚙️ Настройки темпа и LLM:", _load_settings_menu())

    @dp.message(F.text == kb.BTN_TOOLS)
    async def on_menu_tools(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(message, "🧰 Инструменты:", kb.tools_menu())

    @dp.message(F.text == kb.BTN_SOFTS)
    async def on_menu_softs(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        softs, statuses = _softs(), _soft_statuses()
        await _show_section(
            message, render_soft_list(softs, statuses), kb.softs_list_menu(soft_list_rows(softs, statuses))
        )

    @dp.message(F.text == kb.BTN_STATUS)
    async def on_menu_status(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await _show_section(message, render_status(controller, repo), None)

    @dp.message(StateFilter(PostCreation.waiting_content))
    async def on_post_content(message: Message, state: FSMContext) -> None:
        photo = message.photo[-1].file_id if message.photo else None
        video = message.video.file_id if message.video else None
        await state.update_data(
            draft={
                "text": message.text or message.caption or "",
                "photo": photo,
                "video": video,
                "buttons": [],
            }
        )
        await state.set_state(PostCreation.configuring)
        await message.answer("Пост принят. Что дальше?", reply_markup=_configure_keyboard())

    @dp.message(StateFilter(PostCreation.waiting_button))
    async def on_post_button(message: Message, state: FSMContext) -> None:
        button = parse_button_input(message.text or "")
        if button is None:
            await message.answer("Формат: Текст | https://ссылка. Пришли ещё раз.")
            return
        data = await state.get_data()
        draft = data["draft"]
        if len(draft["buttons"]) >= MAX_BUTTONS:
            await message.answer("Достигнут лимит кнопок.")
        else:
            draft["buttons"].append({"text": button.text, "url": button.url})
            await state.update_data(draft=draft)
        await state.set_state(PostCreation.configuring)
        await message.answer(
            f"Кнопка добавлена (всего {len(draft['buttons'])}). Что дальше?",
            reply_markup=_configure_keyboard(),
        )

    @dp.message(StateFilter(SettingInput.waiting_value))
    async def on_setting_value(message: Message, state: FSMContext) -> None:
        field = (await state.get_data()).get("setting_field")
        value = (message.text or "").strip()
        if field == "interval":
            result = set_check_interval(config_path, value)
        elif field == "freshness":
            result = set_schedule_number(
                config_path, value, field="publish_freshness_hours", label="Окно свежести (часов)"
            )
        elif field == "maxposts":
            result = set_schedule_number(
                config_path, value, field="max_posts_per_day", label="Лимит постов в день"
            )
        else:
            result = "Неизвестная настройка."
        await state.clear()
        await _edit_menu_message(message, "⚙️ Настройки темпа и LLM:\n\n" + result, _load_settings_menu())

    @dp.message(StateFilter(AddSourceInput.waiting))
    async def on_add_source_value(message: Message, state: FSMContext) -> None:
        await state.clear()
        result = add_source(repo, message.text or "")
        await _edit_menu_message(
            message, "📰 Источники (тап — вкл/выкл):\n\n" + result, kb.sources_menu(repo.list_sources())
        )

    @dp.message(StateFilter(ChannelSettingInput.waiting_value))
    async def on_channel_setting_value(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        channel_id = data.get("channel_id")
        field = data.get("setting_field")
        set_channel_setting(repo, channel_id, field, message.text or "")
        await state.clear()
        channel = repo.get_channel(channel_id)
        if channel is None:
            await _edit_menu_message(message, "Канал не найден.", None)
            return
        settings = ChannelSettings.from_json(channel.settings_json)
        await _edit_menu_message(
            message, render_channel_card(repo, channel_id), kb.channel_card_menu(channel, settings)
        )

    @dp.callback_query(F.data == "mp:cancel")
    async def on_mp_cancel(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.clear()
        await cb.message.answer("Отменено.")
        await cb.answer()

    @dp.callback_query(F.data == "mp:addbtn")
    async def on_mp_addbtn(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.set_state(PostCreation.waiting_button)
        await cb.message.answer("Пришли кнопку в формате: Текст | https://ссылка")
        await cb.answer()

    @dp.callback_query(F.data == "mp:preview")
    async def on_mp_preview(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        draft = (await state.get_data()).get("draft")
        if not draft:
            await cb.answer("Черновик пуст", show_alert=True)
            return
        buttons = [PostButton(**b) for b in draft["buttons"]]
        await _send_post(cb.message.bot, cb.message.chat.id, draft, buttons)
        await cb.answer("Превью выше")

    @dp.callback_query(F.data == "mp:uniq")
    async def on_mp_uniq(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        draft = (await state.get_data()).get("draft")
        if not draft or not draft["text"].strip():
            await cb.answer("Нет текста для уникализации", show_alert=True)
            return
        await cb.answer("Уникализирую…")
        try:
            new_text = await uniquify_post_text(config_path, draft["text"])
        except Exception as error:  # LLM недоступна/лимит — не роняем бот
            logger.exception("Уникализация текста ручного поста не удалась")
            await cb.message.answer(f"Не вышло уникализировать: {error}")
            return
        draft["text"] = new_text
        await state.update_data(draft=draft)
        await cb.message.answer(
            "🔀 Текст уникализирован:\n\n" + new_text, reply_markup=_configure_keyboard()
        )

    @dp.callback_query(F.data == "mp:publish")
    async def on_mp_publish(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        draft = (await state.get_data()).get("draft")
        if not draft:
            await cb.answer("Черновик пуст", show_alert=True)
            return
        buttons = [PostButton(**b) for b in draft["buttons"]]
        chat_id = load_config(config_path).publishing.telegram.destination
        try:
            await _send_post(cb.message.bot, chat_id, draft, buttons)
        except Exception as error:  # канал недоступен/нет прав — не роняем бот
            logger.exception("Ручная публикация не удалась")
            await cb.message.answer(f"❌ Не опубликовалось: {error}")
            await cb.answer()
            return
        await state.clear()
        await cb.message.answer(f"✅ Опубликовано в {chat_id}.")
        await cb.answer()

    # --- Callback-роутинг разделов меню ---
    @dp.callback_query(F.data == "menu:close")
    async def on_menu_close(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        menu_messages.pop(cb.message.chat.id, None)
        try:
            await cb.message.edit_text("✖️ Меню закрыто. /menu — открыть снова.")
        except TelegramBadRequest:
            pass
        await cb.answer()

    @dp.callback_query(F.data == "auto:run")
    async def on_auto_run(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        started = controller.start()
        await _edit_current(cb, _autoposting_text(), kb.autoposting_menu(running=True))
        await cb.answer("🟢 Запущен" if started else "Уже запущен")

    @dp.callback_query(F.data == "auto:stop")
    async def on_auto_stop(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        stopped = controller.stop()
        await _edit_current(cb, _autoposting_text(), kb.autoposting_menu(running=False))
        await cb.answer("🔴 Остановлен" if stopped else "Уже остановлен")

    @dp.callback_query(F.data == "auto:status")
    async def on_auto_status(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        text = _autoposting_text() + "\n\n" + render_status(controller, repo)
        await _edit_current(cb, text, kb.autoposting_menu(running=controller.is_running()))
        await cb.answer()

    @dp.callback_query(F.data == "auto:queue")
    async def on_auto_queue(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        text = _autoposting_text() + "\n\n" + render_queue(repo)
        await _edit_current(cb, text, kb.autoposting_menu(running=controller.is_running()))
        await cb.answer()

    @dp.callback_query(F.data == "auto:publish")
    async def on_auto_publish(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await cb.answer("Публикую…")
        result = await publish_now(repo, load_config(config_path))
        await _edit_current(
            cb, _autoposting_text() + "\n\n" + result,
            kb.autoposting_menu(running=controller.is_running()),
        )

    @dp.callback_query(F.data == "set:interval")
    async def on_set_interval(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.set_state(SettingInput.waiting_value)
        await state.update_data(setting_field="interval")
        await _edit_current(cb, "⏱ Пришли число минут (как часто проверять каналы):", None)
        await cb.answer()

    @dp.callback_query(F.data == "set:freshness")
    async def on_set_freshness(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.set_state(SettingInput.waiting_value)
        await state.update_data(setting_field="freshness")
        await _edit_current(cb, "🕐 Пришли число часов (окно свежести/бэклога):", None)
        await cb.answer()

    @dp.callback_query(F.data == "set:maxposts")
    async def on_set_maxposts(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.set_state(SettingInput.waiting_value)
        await state.update_data(setting_field="maxposts")
        await _edit_current(cb, "📈 Пришли число (лимит постов в день):", None)
        await cb.answer()

    @dp.callback_query(F.data == "set:provider")
    async def on_set_provider(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        current = load_config(config_path).llm.provider
        await _edit_current(cb, "🧠 Выбери LLM-провайдера:", kb.provider_menu(sorted(LLM_PROVIDERS), current))
        await cb.answer()

    @dp.callback_query(F.data == "set:photodesign")
    async def on_set_photodesign(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        result = toggle_photo_design(repo, load_config(config_path))
        await _edit_current(cb, "⚙️ Настройки темпа и LLM:\n\n" + result, _load_settings_menu())
        await cb.answer("Переключено")

    @dp.callback_query(F.data.startswith("prov:"))
    async def on_provider_pick(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        provider = cb.data.split(":", 1)[1]
        result = switch_provider(config_path, provider)
        await _edit_current(cb, "⚙️ Настройки темпа и LLM:\n\n" + result, _load_settings_menu())
        await cb.answer()

    @dp.callback_query(F.data.startswith("src:toggle:"))
    async def on_src_toggle(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        source_id = int(cb.data.split(":")[2])
        source = repo.get_source(source_id)
        if source is not None:
            repo.update_source(source_id, enabled=not source.enabled)
        await _edit_current(cb, "📰 Источники (тап — вкл/выкл):", kb.sources_menu(repo.list_sources()))
        await cb.answer("Переключено")

    @dp.callback_query(F.data == "src:add")
    async def on_src_add(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        await state.set_state(AddSourceInput.waiting)
        await _edit_current(
            cb,
            "➕ Пришли источник: <tg|vk> <url или group_id> <имя>\nНапример: tg https://t.me/novosti_efir Новости",
            None,
        )
        await cb.answer()

    # --- Управление каналами (мультиканальность) ---
    async def _show_channel_card(cb: CallbackQuery, channel_id: int) -> None:
        channel = repo.get_channel(channel_id)
        if channel is None:
            await cb.answer("Канал не найден", show_alert=True)
            return
        settings = ChannelSettings.from_json(channel.settings_json)
        await _edit_current(
            cb, render_channel_card(repo, channel_id), kb.channel_card_menu(channel, settings)
        )

    @dp.callback_query(F.data == "ch:list")
    async def on_ch_list(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await _edit_current(cb, render_channels(repo), kb.channels_menu(repo.list_channels()))
        await cb.answer()

    @dp.callback_query(F.data.startswith("ch:open:"))
    async def on_ch_open(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await _show_channel_card(cb, int(cb.data.split(":")[2]))
        await cb.answer()

    @dp.callback_query(F.data.startswith("ch:toggle:"))
    async def on_ch_toggle(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        channel_id = int(cb.data.split(":")[2])
        result = toggle_channel(repo, channel_id)
        await _show_channel_card(cb, channel_id)
        await cb.answer(result)

    @dp.callback_query(F.data.startswith("ch:filter:"))
    async def on_ch_filter(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        channel_id = int(cb.data.split(":")[2])
        result = toggle_channel_filter(repo, channel_id)
        await _show_channel_card(cb, channel_id)
        await cb.answer(result)

    @dp.callback_query(F.data.startswith("ch:sources:"))
    async def on_ch_sources(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        channel_id = int(cb.data.split(":")[2])
        await _edit_current(
            cb,
            "📰 Источники канала (тап — вкл/выкл):",
            kb.sources_menu(repo.list_sources(channel_id=channel_id)),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("ch:set:"))
    async def on_ch_set(cb: CallbackQuery, state: FSMContext) -> None:
        if not await _callback_guard(cb):
            return
        _, _, channel_id, field = cb.data.split(":")
        await state.set_state(ChannelSettingInput.waiting_value)
        await state.update_data(channel_id=int(channel_id), setting_field=field)
        labels = {
            "maxposts": "лимит постов/день",
            "interval": "интервал в минутах",
            "films": "сколько фильмов качать в день",
            "clips": "сколько клипов резать из каждого фильма",
        }
        label = labels.get(field, "значение")
        await _edit_current(cb, f"Пришли число — {label}:", None)
        await cb.answer()

    @dp.callback_query(F.data == "tools:uniq")
    async def on_tools_uniq(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await cb.answer(
            "Пришли видео или фото файлом (до 20 МБ) — верну 5 уникальных версий.",
            show_alert=True,
        )

    @dp.callback_query(F.data == "tools:nature")
    async def on_tools_nature(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await _edit_current(cb, render_nature_status(nature_controller), kb.process_menu("nature"))
        await cb.answer()

    @dp.callback_query(F.data == "tools:shorts")
    async def on_tools_shorts(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await _edit_current(cb, render_shorts_status(shorts_controller), kb.process_menu("shorts"))
        await cb.answer()

    @dp.callback_query(F.data.startswith("nature:"))
    async def on_nature_action(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        action = cb.data.split(":", 1)[1]
        if nature_controller is not None and action == "run":
            nature_controller.start()
        elif nature_controller is not None and action == "stop":
            nature_controller.stop()
        await _edit_current(cb, render_nature_status(nature_controller), kb.process_menu("nature"))
        await cb.answer()

    @dp.callback_query(F.data.startswith("shorts:"))
    async def on_shorts_action(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        action = cb.data.split(":", 1)[1]
        if shorts_controller is not None and action == "run":
            shorts_controller.start()
        elif shorts_controller is not None and action == "stop":
            shorts_controller.stop()
        await _edit_current(cb, render_shorts_status(shorts_controller), kb.process_menu("shorts"))
        await cb.answer()

    async def _show_soft_list(cb: CallbackQuery) -> None:
        softs, statuses = _softs(), _soft_statuses()
        await _edit_current(
            cb, render_soft_list(softs, statuses), kb.softs_list_menu(soft_list_rows(softs, statuses))
        )

    async def _show_soft(cb: CallbackQuery, soft: Soft, header: str | None = None) -> None:
        running = _soft_running(soft)
        text = header if header is not None else f"{'🟢' if running else '⚪'} {soft.title}"
        await _edit_current(
            cb, text, kb.soft_menu(soft.soft_id, kind=soft.kind, running=running, channel_id=soft.channel_id)
        )

    @dp.callback_query(F.data == "soft:list")
    async def on_soft_list(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await _show_soft_list(cb)
        await cb.answer()

    @dp.callback_query(F.data.startswith("soft:open:"))
    async def on_soft_open(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        soft = find_soft(_softs(), cb.data.split(":", 2)[2])
        if soft is None:
            await cb.answer("Софт не найден", show_alert=True)
            return
        await _show_soft(cb, soft)
        await cb.answer()

    @dp.callback_query(F.data.startswith("soft:on:"))
    async def on_soft_on(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        soft = find_soft(_softs(), cb.data.split(":", 2)[2])
        if soft is None:
            await cb.answer("Софт не найден", show_alert=True)
            return
        ok = _set_soft_running(soft, True)
        await _show_soft(cb, soft)
        await cb.answer("🟢 Включён" if ok else "Управление этим софтом ещё не настроено (следующий срез)")

    @dp.callback_query(F.data.startswith("soft:off:"))
    async def on_soft_off(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        soft = find_soft(_softs(), cb.data.split(":", 2)[2])
        if soft is None:
            await cb.answer("Софт не найден", show_alert=True)
            return
        ok = _set_soft_running(soft, False)
        await _show_soft(cb, soft)
        await cb.answer("🔴 Выключен" if ok else "Управление этим софтом ещё не настроено (следующий срез)")

    @dp.callback_query(F.data.startswith("soft:status:"))
    async def on_soft_status(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        soft = find_soft(_softs(), cb.data.split(":", 2)[2])
        if soft is None:
            await cb.answer("Софт не найден", show_alert=True)
            return
        await _show_soft(cb, soft, header=_soft_status_text(soft))
        await cb.answer()

    @dp.callback_query(F.data.startswith("soft:dests:"))
    async def on_soft_dests(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        channel_id = int(cb.data.split(":", 2)[2])
        channel = repo.get_channel(channel_id)
        if channel is None:
            await cb.answer("Канал не найден", show_alert=True)
            return
        text = (
            f"📢 Каналы публикации «{channel.name}»:\n"
            f"TG: {channel.tg_destination or '—'}\n"
            f"VK: {channel.vk_destination or '—'}"
        )
        soft = find_soft(_softs(), f"ch_{channel_id}")
        await _show_soft(cb, soft, header=text)
        await cb.answer()

    @dp.callback_query(F.data.startswith("soft:na:"))
    async def on_soft_na(cb: CallbackQuery) -> None:
        if not await _callback_guard(cb):
            return
        await cb.answer(
            "Пока настраивается внутри самого софта. Полное управление извне — следующий срез.",
            show_alert=True,
        )

    @dp.message(F.video | F.photo | F.document)
    async def on_media(message: Message) -> None:
        if not await guard(message):
            return
        await _handle_uniquify(message.bot, message)

    return dp


async def _handle_uniquify(bot, message) -> None:
    """Скачивает присланное медиа, делает 5 уникальных версий, отправляет файлами
    (документами — чтобы Telegram не пережимал и качество сохранилось)."""
    from aiogram.types import FSInputFile

    file_id, filename = _extract_media(message)
    if file_id is None:
        await message.answer("Пришли видео или фото файлом (до 20 МБ).")
        return

    UNIQUIFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    input_path = UNIQUIFY_INPUT_DIR / filename
    await message.answer("Уникализирую… (может занять до минуты)")
    try:
        await bot.download(file_id, destination=str(input_path))
        variants = uniquify_media_file(input_path)
    except MediaUniquifyError as error:
        await message.answer(f"Не получилось: {error}")
        return
    except Exception as error:  # сеть/скачивание — не роняем бот
        logger.exception("Уникализация не удалась")
        await message.answer(f"Ошибка обработки: {error}")
        return

    for path in variants:
        await message.answer_document(FSInputFile(path))
    await message.answer(f"Готово — {len(variants)} уникальных версий.")


def _extract_media(message) -> tuple[object | None, str]:
    """Возвращает (file_id-объект для download, имя файла) для видео/фото/документа."""
    if getattr(message, "video", None):
        return message.video, (message.video.file_name or f"{message.video.file_unique_id}.mp4")
    if getattr(message, "document", None):
        return message.document, (message.document.file_name or f"{message.document.file_unique_id}.bin")
    if getattr(message, "photo", None):
        largest = message.photo[-1]
        return largest, f"{largest.file_unique_id}.jpg"
    return None, ""


def _build_post_markup(buttons: list[PostButton]):
    """InlineKeyboardMarkup из URL-кнопок под постом, либо None если кнопок нет."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=b.text, url=b.url)] for b in buttons]
    )


async def _send_post(bot, chat_id, draft: dict, buttons: list[PostButton]) -> None:
    """Отправляет собранный пост в chat_id (канал при публикации или владельцу для
    превью). Медиа пересылается по file_id — этот же бот и получил файл, и публикует,
    поэтому file_id переиспользуется без повторной загрузки."""
    markup = _build_post_markup(buttons)
    text = (draft.get("text") or "").strip()
    if draft.get("video"):
        await bot.send_video(chat_id, draft["video"], caption=text or None, reply_markup=markup)
    elif draft.get("photo"):
        await bot.send_photo(chat_id, draft["photo"], caption=text or None, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text or "(пусто)", reply_markup=markup)


async def uniquify_post_text(config_path, text: str) -> str:
    """Антиплагиат-рерайт текста ручного поста через тот же rewrite_post, что и
    основной пайплайн. strip_markdown — чтобы **/* из рерайта не уходили в канал
    сырыми. source="" — источник не упоминаем (это ручной пост владельца)."""
    import asyncio

    from app.core.llm.client import LLMClient
    from app.core.llm.rewriter import rewrite_post
    from app.core.publishing.text_formatting import strip_markdown

    config = load_config(config_path)
    client = LLMClient(config.llm)
    rewritten = await asyncio.to_thread(
        rewrite_post,
        client,
        text=text,
        source="",
        style=config.rewrite.style,
        max_length=max(config.rewrite.max_length_chars, len(text)),
        include_hashtags=False,
    )
    return strip_markdown(rewritten).strip()


async def run_bot() -> None:
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession

    from app.core.publishing.telegram_publisher import detect_proxy_url
    from app.db.repository import Repository as Repo, init_db, make_engine
    from app.manager.repository import ManagerRepository, init_manager_db, make_manager_engine, seed_default_softs
    from app.service_controller import ServiceController

    token = os.environ.get(CONTROL_BOT_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{CONTROL_BOT_TOKEN_ENV} не задан в .env")

    engine = make_engine()
    init_db(engine)
    repo = Repo(engine)

    # Отдельная БД-реестр менеджера (data/manager.db) — не пересекается со схемой
    # Новостей. Идемпотентный сид известных внешних софтов при каждом старте.
    manager_engine = make_manager_engine()
    init_manager_db(manager_engine)
    manager_repo = ManagerRepository(manager_engine)
    seed_default_softs(manager_repo)

    controller = ServiceController()

    # На сервере пайплайн должен работать 24/7 и быть управляемым — AUTOSTART_SERVICE=1
    # поднимает его сразу при старте бота (иначе ждёт команды /run).
    if os.environ.get("AUTOSTART_SERVICE", "").lower() in ("1", "true", "yes"):
        controller.start()
        logger.info("Пайплайн запущен автоматически (AUTOSTART_SERVICE)")

    proxy_url = detect_proxy_url()
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    bot = Bot(token=token, session=session)
    dp = build_dispatcher(
        controller,
        repo,
        nature_controller=build_nature_controller(),
        shorts_controller=build_shorts_controller(),
        shorts_base_url=os.environ.get(SHORTS_BASE_URL_ENV, SHORTS_DEFAULT_BASE_URL),
        manager_repo=manager_repo,
    )

    logger.info("Control-бот запущен (long polling)")
    await dp.start_polling(bot)


def main() -> None:
    import asyncio

    from dotenv import load_dotenv

    from app.logging_setup import setup_logging

    load_dotenv()
    setup_logging(load_config().logging)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
