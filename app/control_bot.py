"""Telegram-бот управления софтом. Отдельный бот (свой токен CONTROL_BOT_TOKEN,
не путать с TG_BOT_TOKEN, которым публикуются посты в канал).

Команды: /run, /stop, /status, /publish, /queue, /provider — удалённое управление
сервисом. Доступ только у владельца (первый, кто нажал /start, либо CONTROL_BOT_OWNER_ID).

Логика команд вынесена в чистые функции (render_*/switch_provider/publish_now) — они
тестируются без aiogram; сам aiogram-слой (build_dispatcher/run_bot) — тонкая обвязка.
"""
from __future__ import annotations

import logging
import os

from pathlib import Path

from app.config.loader import CONFIG_PATH, AppConfig, load_config, update_config_section
from app.core.media.uniquifier import MediaUniquifyError, uniquify_media
from app.core.publishing.footer import build_footer_links_from_config
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.core.scheduler import pick_next_post_to_publish
from app.db.repository import Repository
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
    "/run — запустить сервис\n"
    "/stop — остановить сервис\n"
    "/status — статус + последние публикации\n"
    "/publish — опубликовать лучший пост из очереди сейчас\n"
    "/queue — сколько постов в очереди\n"
    "/provider <groq|openrouter|gemini|ollama> — сменить LLM\n"
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


def is_authorized(repo: Repository, user_id: int) -> bool:
    owner = get_owner_id(repo)
    return owner is not None and owner == user_id


def handle_start(repo: Repository, user_id: int) -> str:
    """Первый /start без настроенного владельца — регистрирует отправителя владельцем."""
    if get_owner_id(repo) is None:
        register_owner(repo, user_id)
        return "Вы зарегистрированы как владелец бота.\n\n" + HELP_TEXT
    if is_authorized(repo, user_id):
        return HELP_TEXT
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


async def publish_now(repo: Repository, config: AppConfig) -> str:
    post = pick_next_post_to_publish(
        repo,
        max_posts_per_day=config.publishing.schedule.max_posts_per_day,
        important_score_threshold=config.filters.important_score_threshold,
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
        await message.answer(handle_start(repo, message.from_user.id))

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

    @dp.message(Command("publish"))
    async def on_publish(message: Message) -> None:
        if await guard(message):
            await message.answer("Публикую…")
            await message.answer(await publish_now(repo, load_config(config_path)))

    @dp.message(Command("provider"))
    async def on_provider(message: Message) -> None:
        if not await guard(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажи провайдера: /provider groq")
            return
        await message.answer(switch_provider(config_path, parts[1]))

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


async def run_bot() -> None:
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession

    from app.core.publishing.telegram_publisher import detect_proxy_url
    from app.db.repository import Repository as Repo, init_db, make_engine
    from app.service_controller import ServiceController

    token = os.environ.get(CONTROL_BOT_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{CONTROL_BOT_TOKEN_ENV} не задан в .env")

    engine = make_engine()
    init_db(engine)
    repo = Repo(engine)
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
