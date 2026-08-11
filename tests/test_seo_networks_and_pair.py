"""SEO уходит только в VK, и TG не убегает вперёд, когда личный токен VK занят.

Обе вещи — по жалобам владельца 2026-08-11: «в тг больше постов про кино, а вк меньше,
и за эту ночь в вк не было публикаций» и «в тг, сео не надо, только в вк».
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import headless_service
from app.core.channel_settings import ChannelSettings
from app.core.publishing.queue_service import _build_publish_text
from app.core.publishing.vk_queue_service import _build_vk_publish_text
from app.core.seo.builder import SeoProfile, build_search_line

TEXT_WITH_TAGS = "Дроны атаковали Ростов\n\nПодробности уточняются.\n\n#ростов #новости"


def test_vk_keeps_the_seo_tag_line():
    text = _build_vk_publish_text(None, TEXT_WITH_TAGS, None, include_hashtags=True)

    assert "#ростов #новости" in text


def test_telegram_drops_the_seo_tag_line():
    """Поиск Telegram текст постов не индексирует — строка тегов там только съедает
    лимит подписи в 1024 знака."""
    text = _build_publish_text(None, TEXT_WITH_TAGS, None, include_hashtags=False)

    assert "#ростов" not in text
    assert "Дроны атаковали Ростов" in text


def test_channel_phrases_go_into_search_line_even_without_names():
    """Общие запросы канала не зависят от того, нашлись ли в тексте имена собственные."""
    profile = SeoProfile(
        search_phrases=["{q} смотреть онлайн"],
        channel_phrases=["кино", "фильмы смотреть онлайн"],
    )

    line = build_search_line("...", profile)

    assert "кино" in line
    assert "фильмы смотреть онлайн" in line


def test_channel_phrases_join_the_name_based_ones():
    profile = SeoProfile(
        search_phrases=["{q} смотреть онлайн"],
        channel_phrases=["кино"],
    )

    line = build_search_line("Фильм с Робертом Дауни вышел в прокат", profile)

    assert "Роберт" in line or "Дауни" in line
    assert line.endswith("кино")


def test_channel_phrases_survive_json_roundtrip():
    settings = ChannelSettings(seo_enabled=True, seo_channel_phrases=["кино", "фильмы"])

    restored = ChannelSettings.from_json(settings.to_json())

    assert restored.seo_channel_phrases == ["кино", "фильмы"]
    assert restored.seo_profile().channel_phrases == ["кино", "фильмы"]


class _Processed:
    def __init__(self, image_paths=None, video_path=None):
        self.image_paths = json.dumps(image_paths) if image_paths else None
        self.video_path = video_path


class _Repo:
    def __init__(self, processed):
        self._processed = processed

    def get_processed_post(self, post_id):
        return self._processed


class _Channel:
    id = 1
    name = "КиноЛайф"
    settings_json = json.dumps({"vk_upload_token_envs": ["VK_UPLOAD_TOKEN_1"]})


class _Pool:
    def __init__(self, free: bool) -> None:
        self._free = free

    def has_free_account(self):
        return self._free


def _settings() -> ChannelSettings:
    return ChannelSettings.from_json(_Channel.settings_json)


def test_post_with_media_waits_when_the_pool_is_busy(monkeypatch):
    """Ровно этот случай и разводил сети: VK откладывал пост с медиа, TG уже вышел."""
    monkeypatch.setattr(headless_service, "build_channel_token_pool", lambda ch: _Pool(free=False))
    repo = _Repo(_Processed(image_paths=["a.jpg"]))

    assert headless_service._vk_upload_unavailable(repo, _Channel(), _settings(), 1) is True


def test_post_without_media_goes_through_a_busy_pool(monkeypatch):
    """Пост без вложений публикуется групповым токеном и пул не трогает вовсе —
    задерживать его значило бы останавливать канал на ровном месте."""
    monkeypatch.setattr(headless_service, "build_channel_token_pool", lambda ch: _Pool(free=False))
    repo = _Repo(_Processed())

    assert headless_service._vk_upload_unavailable(repo, _Channel(), _settings(), 1) is False


def test_free_pool_does_not_block_anything(monkeypatch):
    monkeypatch.setattr(headless_service, "build_channel_token_pool", lambda ch: _Pool(free=True))
    repo = _Repo(_Processed(image_paths=["a.jpg"]))

    assert headless_service._vk_upload_unavailable(repo, _Channel(), _settings(), 1) is False


def test_channel_without_require_media_is_not_gated(monkeypatch):
    """Там, где пост с медиа штатно выходит текстом, VK не отстанет — разводить нечего."""
    monkeypatch.setattr(
        headless_service, "build_channel_token_pool", lambda ch: pytest.fail("пул не нужен")
    )
    settings = ChannelSettings(require_media=False)
    repo = _Repo(_Processed(image_paths=["a.jpg"]))

    assert headless_service._vk_upload_unavailable(repo, _Channel(), settings, 1) is False


def test_channel_without_pool_is_not_gated(monkeypatch):
    monkeypatch.setattr(headless_service, "build_channel_token_pool", lambda ch: None)
    repo = _Repo(_Processed(image_paths=["a.jpg"]))

    assert headless_service._vk_upload_unavailable(repo, _Channel(), _settings(), 1) is False


def test_busy_pool_stops_the_whole_pair(monkeypatch):
    """Сквозная проверка: при занятом пуле не публикуется НИ ОДНА сеть."""
    published: list[str] = []

    async def fake_tg(*args, **kwargs):
        published.append("tg")

    monkeypatch.setattr(headless_service, "publish_queued_post", fake_tg)
    monkeypatch.setattr(
        headless_service, "publish_queued_post_vk", lambda *a, **k: published.append("vk")
    )
    monkeypatch.setattr(headless_service, "_vk_upload_unavailable", lambda *a, **k: True)
    monkeypatch.setattr(headless_service, "check_publish_allowed", lambda *a, **k: None)

    asyncio.run(_run_pair(monkeypatch))

    assert published == []


async def _run_pair(monkeypatch):
    class _FullRepo(_Repo):
        def get_published_network_at(self, post_id, network):
            return None

    class _Breaker:
        def is_open(self, *args):
            return False

    class _Config:
        class publishing:
            class schedule:
                max_posts_per_day = 10
                min_interval_minutes = 60
                publish_freshness_hours = 24

            class telegram:
                enabled = True

            class vk:
                enabled = True
                token_env = "VK_GROUP_TOKEN"

        class rewrite:
            include_hashtags = False

        footer = None

    channel = _Channel()
    channel.tg_destination = "@chan"
    channel.vk_destination = "1"
    channel.vk_token_env = "VK_GROUP_TOKEN"

    await headless_service._publish_channel_post(
        _FullRepo(_Processed(image_paths=["a.jpg"])),
        channel,
        post_id=1,
        config=_Config(),
        tg_publisher=object(),
        vk_publisher=object(),
        footer_links=None,
        breaker=_Breaker(),
    )
