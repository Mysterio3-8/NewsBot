import shutil

import pytest

from app.config.loader import CONFIG_PATH, load_config
from app.factories import (
    build_telegram_fetcher,
    build_telegram_publisher,
    build_vk_fetcher,
    build_vk_publisher,
)


@pytest.fixture
def config(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return load_config(dest)


def test_build_telegram_publisher_returns_none_without_token(config, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    assert build_telegram_publisher(config) is None


def test_build_telegram_publisher_returns_instance_with_token(config, monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "123456789:AAFakeTokenForTests1234567890abcdefghi")
    assert build_telegram_publisher(config) is not None


def test_build_vk_publisher_returns_none_without_token(config, monkeypatch):
    monkeypatch.delenv("VK_GROUP_TOKEN", raising=False)
    assert build_vk_publisher(config) is None


def test_build_vk_publisher_returns_instance_with_token(config, monkeypatch):
    monkeypatch.setenv("VK_GROUP_TOKEN", "fake-token")
    assert build_vk_publisher(config) is not None


def test_build_telegram_fetcher_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert build_telegram_fetcher() is None


def test_build_telegram_fetcher_returns_instance_with_credentials(monkeypatch):
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "fakehash")
    assert build_telegram_fetcher() is not None


def test_build_vk_fetcher_returns_none_without_token(monkeypatch):
    monkeypatch.delenv("VK_USER_TOKEN", raising=False)
    assert build_vk_fetcher() is None


def test_build_vk_fetcher_returns_instance_with_token(monkeypatch):
    monkeypatch.setenv("VK_USER_TOKEN", "fake-user-token")
    assert build_vk_fetcher() is not None
