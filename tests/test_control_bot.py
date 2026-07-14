"""Control-бот: тестируем чистую логику команд (auth, status, queue, provider, publish)
без aiogram-раннера. aiogram-обвязка (build_dispatcher) — тонкая, отдельно не мокается."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

import app.control_bot as bot
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


@pytest.fixture(autouse=True)
def clear_owner_env(monkeypatch):
    monkeypatch.delenv(bot.CONTROL_BOT_OWNER_ENV, raising=False)


def test_first_start_registers_owner(tmp_path):
    repo = make_repo(tmp_path)
    reply = bot.handle_start(repo, user_id=555)
    assert "владелец" in reply.lower()
    assert bot.get_owner_id(repo) == 555
    assert bot.is_authorized(repo, 555) is True


def test_second_user_is_rejected_after_owner_registered(tmp_path):
    repo = make_repo(tmp_path)
    bot.handle_start(repo, user_id=555)
    assert bot.is_authorized(repo, 999) is False
    assert "запрещ" in bot.handle_start(repo, user_id=999).lower()


def test_owner_id_from_env_takes_precedence(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv(bot.CONTROL_BOT_OWNER_ENV, "42")
    assert bot.get_owner_id(repo) == 42
    assert bot.is_authorized(repo, 42) is True
    assert bot.is_authorized(repo, 43) is False


def test_render_status_reports_running_and_recent(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="x")
    import datetime
    p = repo.create_processed_post(raw_post_id=raw.id, score=90, headline="Заголовок", status="queued")
    repo.update_processed_post_status(p.id, "published", published_at=datetime.datetime(2026, 7, 4, 9, 40))

    controller = Mock()
    controller.is_running.return_value = True
    controller.started_at = None

    status = bot.render_status(controller, repo)
    assert "запущен" in status
    assert "Заголовок" in status
    assert "04.07.2026 12:40" in status  # UTC 09:40 -> МСК 12:40


def test_render_queue_counts_posts_and_images(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw1 = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw2 = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    repo.create_processed_post(raw_post_id=raw1.id, score=90, image_paths=["img.jpg"], status="queued")
    repo.create_processed_post(raw_post_id=raw2.id, score=80, status="queued")

    result = bot.render_queue(repo)
    assert "2 постов" in result
    assert "1 с картинкой" in result


def test_switch_provider_rejects_unknown(tmp_path):
    result = bot.switch_provider(tmp_path / "config.yaml", "gpt-9000")
    assert "Неизвестн" in result


def test_switch_provider_updates_config(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(bot, "update_config_section", lambda path, section, **f: calls.update(section=section, fields=f))
    result = bot.switch_provider(tmp_path / "config.yaml", "OpenRouter")
    assert calls["section"] == "llm"
    assert calls["fields"] == {"provider": "openrouter"}
    assert "openrouter" in result


def test_uniquify_media_file_delegates_with_default_count(tmp_path, monkeypatch):
    captured = {}

    def fake_uniquify(input_path, *, count, output_dir):
        captured["count"] = count
        return [output_dir / "a.mp4"]

    monkeypatch.setattr(bot, "uniquify_media", fake_uniquify)
    bot.uniquify_media_file(tmp_path / "clip.mp4", output_dir=tmp_path / "out")
    assert captured["count"] == bot.UNIQUIFY_VARIANTS


def test_build_dispatcher_registers_media_handler():
    from unittest.mock import Mock

    dp = bot.build_dispatcher(Mock(), Mock(), "config.yaml")
    # 38 было до управления каналами; +2 message (reply-кнопка «Каналы» + FSM ввода
    # настройки канала) = 40
    assert len(dp.message.handlers) == 40
    # 23 было; +6 callback каналов (ch:list/open/toggle/filter/sources/set) = 29;
    # +1 тумблер оформления фото (set:photodesign) = 30
    assert len(dp.callback_query.handlers) == 30


def test_build_nature_controller_none_without_env_path():
    assert bot.build_nature_controller(env={}) is None


def test_build_nature_controller_builds_from_env_path(tmp_path):
    controller = bot.build_nature_controller(env={bot.NATURE_BOT_PATH_ENV: str(tmp_path)})
    assert controller is not None
    assert controller.name == "nature"
    assert str(tmp_path) in str(controller._cwd)


def test_render_nature_status_reports_not_configured():
    result = bot.render_nature_status(None)
    assert "не настроен" in result
    assert bot.NATURE_BOT_PATH_ENV in result


def test_render_nature_status_reports_running():
    controller = Mock()
    controller.is_running.return_value = True
    controller.started_at = None
    controller.tail_log.return_value = "log line"
    result = bot.render_nature_status(controller)
    assert "запущен" in result
    assert "log line" in result


def test_build_shorts_controller_none_without_env_path():
    assert bot.build_shorts_controller(env={}) is None


def test_build_shorts_controller_builds_from_env_path(tmp_path):
    controller = bot.build_shorts_controller(env={bot.SHORTS_PATH_ENV: str(tmp_path)})
    assert controller is not None
    assert controller.name == "shorts"


def test_render_shorts_status_reports_not_configured():
    result = bot.render_shorts_status(None)
    assert "не настроен" in result
    assert bot.SHORTS_PATH_ENV in result


@pytest.mark.asyncio
async def test_generate_short_for_post_returns_error_for_missing_post(tmp_path):
    repo = make_repo(tmp_path)
    result = await bot.generate_short_for_post(repo, post_id=999, base_url="http://x")
    assert "не найден" in result


@pytest.mark.asyncio
async def test_generate_short_for_post_returns_error_without_rewritten_text(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="x")
    post = repo.create_processed_post(raw_post_id=raw.id, score=90, headline="H", status="queued")

    result = await bot.generate_short_for_post(repo, post_id=post.id, base_url="http://x")
    assert "нет текста рерайта" in result


@pytest.mark.asyncio
async def test_generate_short_for_post_downloads_video_on_success(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="x")
    post = repo.create_processed_post(
        raw_post_id=raw.id, score=90, headline="Заголовок", status="queued",
        rewritten_text="Текст рерайта",
    )

    monkeypatch.setattr(bot, "SHORTS_OUTPUT_DIR", tmp_path / "shorts")

    from app.core.shorts import client as shorts_client
    monkeypatch.setattr(shorts_client, "create_task", lambda *a, **k: "task-1")
    monkeypatch.setattr(shorts_client, "wait_for_video", lambda *a, **k: ["http://x/final-1.mp4"])
    downloaded = {}
    monkeypatch.setattr(shorts_client, "download_video", lambda url, dest: downloaded.update(url=url, dest=dest))

    result = await bot.generate_short_for_post(repo, post_id=post.id, base_url="http://127.0.0.1:8080")

    assert downloaded["url"] == "http://x/final-1.mp4"
    assert result == tmp_path / "shorts" / f"{post.id}_task-1.mp4"


@pytest.mark.asyncio
async def test_generate_short_for_post_reports_shorts_client_error(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="x")
    post = repo.create_processed_post(
        raw_post_id=raw.id, score=90, headline="Заголовок", status="queued",
        rewritten_text="Текст рерайта",
    )

    from app.core.shorts import client as shorts_client

    def raise_error(*a, **k):
        raise shorts_client.ShortsClientError("сервис недоступен")

    monkeypatch.setattr(shorts_client, "create_task", raise_error)

    result = await bot.generate_short_for_post(repo, post_id=post.id, base_url="http://127.0.0.1:8080")
    assert "Не получилось" in result


@pytest.mark.asyncio
async def test_publish_now_empty_queue(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(bot, "pick_next_post_to_publish", lambda *a, **k: None)
    config = Mock()
    config.publishing.schedule.max_posts_per_day = 6
    config.filters.important_score_threshold = 65
    result = await bot.publish_now(repo, config)
    assert "Нет постов" in result


@pytest.mark.asyncio
async def test_publish_now_publishes_and_reports(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    post = Mock(id=7, headline="Тестовый пост")
    monkeypatch.setattr(bot, "pick_next_post_to_publish", lambda *a, **k: post)
    monkeypatch.setattr(bot, "build_footer_links_from_config", lambda footer: None)

    tg = AsyncMock(spec=TelegramPublisher)
    monkeypatch.setattr(bot, "build_telegram_publisher", lambda config: tg)
    monkeypatch.setattr(bot, "build_vk_publisher", lambda config: None)

    async def fake_publish_tg(*a, **k):
        return PublishResult(success=True, message_id=1, error=None)

    monkeypatch.setattr(bot, "publish_queued_post", fake_publish_tg)

    config = Mock()
    config.publishing.schedule.max_posts_per_day = 6
    config.publishing.schedule.min_interval_minutes = 180
    config.filters.important_score_threshold = 65
    config.publishing.telegram.enabled = True
    config.publishing.telegram.destination = "@channel"
    config.publishing.vk.enabled = True
    config.rewrite.include_hashtags = False

    result = await bot.publish_now(repo, config)
    assert "Тестовый пост" in result
    assert "TG: ✅" in result


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "sources.db")
    init_db(engine)
    return Repository(engine)


def test_render_sources_lists_with_status(tmp_path):
    repo = _repo(tmp_path)
    s1 = repo.create_source(type="tg", name="novosti_efir", url="https://t.me/novosti_efir")
    repo.create_source(type="vk", name="postnews", url="152992737")
    repo.update_source(s1.id, enabled=False)

    text = bot.render_sources(repo)

    assert "novosti_efir" in text
    assert "postnews" in text
    assert "⚪" in text and "🟢" in text


def test_toggle_source_enables_and_disables(tmp_path):
    repo = _repo(tmp_path)
    src = repo.create_source(type="tg", name="x", url="https://t.me/x")

    bot.toggle_source(repo, str(src.id), enabled=False)
    assert repo.get_source(src.id).enabled is False

    bot.toggle_source(repo, str(src.id), enabled=True)
    assert repo.get_source(src.id).enabled is True


def test_toggle_source_rejects_non_numeric(tmp_path):
    repo = _repo(tmp_path)
    assert "числовой id" in bot.toggle_source(repo, "abc", enabled=True)


def test_toggle_source_reports_missing(tmp_path):
    repo = _repo(tmp_path)
    assert "не найден" in bot.toggle_source(repo, "999", enabled=True)


def _config_with_design(enabled: bool):
    from types import SimpleNamespace

    return SimpleNamespace(headline_card=SimpleNamespace(enabled=enabled))


def test_photo_design_defaults_to_config_when_no_setting(tmp_path):
    repo = _repo(tmp_path)
    assert bot.is_photo_design_on(repo, _config_with_design(True)) is True
    assert bot.is_photo_design_on(repo, _config_with_design(False)) is False


def test_toggle_photo_design_flips_and_persists(tmp_path):
    repo = _repo(tmp_path)
    config = _config_with_design(True)  # дефолт вкл

    msg = bot.toggle_photo_design(repo, config)  # вкл -> выкл
    assert "выкл" in msg
    assert bot.is_photo_design_on(repo, config) is False

    msg = bot.toggle_photo_design(repo, config)  # выкл -> вкл
    assert "вкл" in msg
    assert bot.is_photo_design_on(repo, config) is True


def test_add_source_creates_disabled(tmp_path):
    repo = _repo(tmp_path)
    result = bot.add_source(repo, "tg https://t.me/newchan Новый канал")

    assert "добавлен" in result
    sources = repo.list_sources()
    assert len(sources) == 1
    assert sources[0].enabled is False
    assert sources[0].name == "Новый канал"


def test_add_source_rejects_bad_format(tmp_path):
    repo = _repo(tmp_path)
    assert "Формат" in bot.add_source(repo, "tg только-url")
    assert "tg или vk" in bot.add_source(repo, "foo https://x Имя")


def test_render_settings_shows_current_values():
    from unittest.mock import Mock

    config = Mock()
    config.monitoring.check_interval_minutes = 20
    config.publishing.schedule.jitter_minutes = 10
    config.publishing.schedule.publish_freshness_hours = 12
    config.publishing.schedule.min_interval_minutes = 10
    config.publishing.schedule.max_posts_per_day = 50

    text = bot.render_settings(config)

    assert "каждые 20 мин" in text
    assert "12 ч" in text
    assert "50" in text


def test_set_check_interval_writes_config(tmp_path):
    import shutil

    from app.config.loader import load_config

    cfg_path = tmp_path / "config.yaml"
    shutil.copy("app/config/config.yaml", cfg_path)

    result = bot.set_check_interval(cfg_path, "35")

    assert "35 мин" in result
    assert load_config(cfg_path).monitoring.check_interval_minutes == 35


def test_set_check_interval_rejects_non_numeric(tmp_path):
    assert "число минут" in bot.set_check_interval(tmp_path / "x.yaml", "abc")


def test_set_schedule_number_writes_freshness(tmp_path):
    import shutil

    from app.config.loader import load_config

    cfg_path = tmp_path / "config.yaml"
    shutil.copy("app/config/config.yaml", cfg_path)

    result = bot.set_schedule_number(
        cfg_path, "8", field="publish_freshness_hours", label="Окно свежести (часов)"
    )

    assert "8" in result
    assert load_config(cfg_path).publishing.schedule.publish_freshness_hours == 8
