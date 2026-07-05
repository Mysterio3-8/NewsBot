from unittest.mock import AsyncMock, Mock

import pytest

import app.core.testpost as testpost
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://vk.com/wall-152992737_8999245", (-152992737, 8999245)),
        ("https://vk.ru/postnews?w=wall-152992737_8999245", (-152992737, 8999245)),
        ("wall1_42", (1, 42)),
        ("просто текст без ссылки", None),
    ],
)
def test_parse_vk_post_ref(text, expected):
    assert testpost.parse_vk_post_ref(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "https://vk.ru/postnews?z=photo-152992737_457687164%2Fa7802a5f1a9aaf40e6",
            (-152992737, 457687164),
        ),
        ("photo1_2", (1, 2)),
        ("https://vk.com/wall-152992737_8999245", None),
    ],
)
def test_extract_photo_ref(text, expected):
    assert testpost.extract_photo_ref(text) == expected


def test_get_or_create_test_source_is_idempotent(tmp_path):
    repo = make_repo(tmp_path)

    first = testpost.get_or_create_test_source(repo)
    second = testpost.get_or_create_test_source(repo)

    assert first.id == second.id
    assert first.enabled is False
    assert len(repo.list_sources(source_type="vk")) == 1


@pytest.mark.asyncio
async def test_post_now_rejects_unparseable_ref(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(testpost, "build_vk_fetcher", lambda: Mock())

    result = await testpost.test_post_now(
        repo, Mock(), Mock(), vk_ref="не ссылка вообще"
    )

    assert "Не понял ссылку" in result


@pytest.mark.asyncio
async def test_post_now_reports_missing_token(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(testpost, "build_vk_fetcher", lambda: None)

    result = await testpost.test_post_now(
        repo, Mock(), Mock(), vk_ref="https://vk.com/wall-1_2"
    )

    assert "VK_USER_TOKEN не задан" in result


@pytest.mark.asyncio
async def test_post_now_builds_processed_post_and_publishes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)

    fetcher = Mock()
    fetcher._api.wall.getById.return_value = [
        {
            "id": 8999245,
            "text": "Исходный текст поста",
            "date": 1_800_000_000,
            "attachments": [],
        }
    ]
    fetcher._download_photos.return_value = []
    monkeypatch.setattr(testpost, "build_vk_fetcher", lambda: fetcher)
    monkeypatch.setattr(testpost, "build_image_providers", lambda: {})
    monkeypatch.setattr(
        testpost, "rewrite_post", lambda *a, **k: "Переписанный текст поста"
    )
    monkeypatch.setattr(
        testpost, "generate_headlines", lambda *a, **k: ["Заголовок теста"]
    )
    monkeypatch.setattr(testpost, "_prepare_images", lambda *a, **k: None)
    monkeypatch.setattr(testpost, "build_footer_links_from_config", lambda footer: None)

    tg = AsyncMock(spec=TelegramPublisher)
    monkeypatch.setattr(testpost, "build_telegram_publisher", lambda config: tg)
    monkeypatch.setattr(testpost, "build_vk_publisher", lambda config: None)
    monkeypatch.setattr(
        testpost,
        "publish_queued_post",
        AsyncMock(return_value=PublishResult(success=True, message_id=1, error=None)),
    )

    config = Mock()
    config.rewrite.style = "viral"
    config.rewrite.max_length_chars = 900
    config.rewrite.include_hashtags = False
    config.images = Mock()
    config.watermark = Mock()
    config.publishing.telegram.enabled = True
    config.publishing.telegram.destination = "@channel"
    config.publishing.vk.enabled = False
    config.publishing.schedule.max_posts_per_day = 12
    config.publishing.schedule.min_interval_minutes = 110

    result = await testpost.test_post_now(
        repo, config, Mock(), vk_ref="https://vk.com/wall-152992737_8999245"
    )

    assert "TG: ✅" in result


@pytest.mark.asyncio
async def test_post_now_ignores_schedule_limits_and_publishes_regardless(tmp_path, monkeypatch):
    """По прямому запросу пользователя (2026-07-05): "тестовые посты должны всегда
    выкладываться" — /testpost не должен ждать дневной лимит/интервал rate_guard,
    даже если конфиг настоящего расписания их бы заблокировал. Основной автоцикл
    (queue_service.py напрямую из check_cycle.py) эти лимиты по-прежнему уважает —
    обход только в этом, ручном, пути."""
    repo = make_repo(tmp_path)

    fetcher = Mock()
    fetcher._api.wall.getById.return_value = [
        {"id": 1, "text": "Текст", "date": 1_800_000_000, "attachments": []}
    ]
    fetcher._download_photos.return_value = []
    monkeypatch.setattr(testpost, "build_vk_fetcher", lambda: fetcher)
    monkeypatch.setattr(testpost, "build_image_providers", lambda: {})
    monkeypatch.setattr(testpost, "rewrite_post", lambda *a, **k: "Текст")
    monkeypatch.setattr(testpost, "generate_headlines", lambda *a, **k: ["Заголовок"])
    monkeypatch.setattr(testpost, "_prepare_images", lambda *a, **k: None)
    monkeypatch.setattr(testpost, "build_footer_links_from_config", lambda footer: None)

    tg = AsyncMock(spec=TelegramPublisher)
    monkeypatch.setattr(testpost, "build_telegram_publisher", lambda config: tg)
    monkeypatch.setattr(testpost, "build_vk_publisher", lambda config: None)
    publish_mock = AsyncMock(
        return_value=PublishResult(success=True, message_id=1, error=None)
    )
    monkeypatch.setattr(testpost, "publish_queued_post", publish_mock)

    config = Mock()
    config.rewrite.style = "viral"
    config.rewrite.max_length_chars = 900
    config.rewrite.include_hashtags = False
    config.images = Mock()
    config.watermark = Mock()
    config.publishing.telegram.enabled = True
    config.publishing.telegram.destination = "@channel"
    config.publishing.vk.enabled = False
    # Реалистичное строгое расписание, которое ДОЛЖНО игнорироваться для тестовых постов.
    config.publishing.schedule.max_posts_per_day = 1
    config.publishing.schedule.min_interval_minutes = 999999

    await testpost.test_post_now(repo, config, Mock(), vk_ref="https://vk.com/wall-1_1")

    _, kwargs = publish_mock.call_args
    assert kwargs["max_posts_per_day"] == testpost.TEST_POST_MAX_PER_DAY
    assert kwargs["min_interval_minutes"] == 0
