"""Проверки продления официального VK-токена.

Главное, что здесь защищается — цепочка refresh-токенов. VK ID выдаёт новый refresh
вместе с каждым access и гасит старый; потеряли новый — цепочка порвана, и владельцу
снова идти входить руками в браузер.
"""

from __future__ import annotations

import pytest

from app.core.publishing import vk_token_refresh


@pytest.fixture()
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "OTHER_SECRET=не трогать",
                "VK_TOKEN_OFFICIAL=старый-доступ",
                "VK_TOKEN_OFFICIAL_REFRESH=старый-refresh",
                "VK_TOKEN_OFFICIAL_DEVICE_ID=устройство",
                "VK_TOKEN_OFFICIAL_CLIENT_ID=54733601",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def environ():
    return {
        "VK_TOKEN_OFFICIAL": "старый-доступ",
        "VK_TOKEN_OFFICIAL_REFRESH": "старый-refresh",
        "VK_TOKEN_OFFICIAL_DEVICE_ID": "устройство",
        "VK_TOKEN_OFFICIAL_CLIENT_ID": "54733601",
    }


def test_refresh_saves_new_pair_to_env_and_process(monkeypatch, env_file, environ):
    monkeypatch.setattr(
        vk_token_refresh,
        "_request_new_token",
        lambda *_: {
            "access_token": "новый-доступ",
            "refresh_token": "новый-refresh",
            "expires_in": 3600,
        },
    )

    assert vk_token_refresh.refresh_official_token(env_path=env_file, environ=environ)

    saved = dict(
        line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if line
    )
    assert saved["VK_TOKEN_OFFICIAL"] == "новый-доступ"
    # Новый refresh обязан заменить старый: старый уже погашен на стороне VK.
    assert saved["VK_TOKEN_OFFICIAL_REFRESH"] == "новый-refresh"
    # Живой процесс читает токен из окружения — без этого продление увидел бы только
    # следующий перезапуск сервиса.
    assert environ["VK_TOKEN_OFFICIAL"] == "новый-доступ"


def test_refresh_keeps_other_secrets_untouched(monkeypatch, env_file, environ):
    monkeypatch.setattr(
        vk_token_refresh,
        "_request_new_token",
        lambda *_: {"access_token": "новый-доступ", "refresh_token": "новый-refresh"},
    )

    vk_token_refresh.refresh_official_token(env_path=env_file, environ=environ)

    assert "OTHER_SECRET=не трогать" in env_file.read_text(encoding="utf-8")


def test_refresh_without_settings_is_not_an_error(env_file):
    """Токен не заводили — софт живёт на прежнем ключе, падать не из-за чего."""
    assert vk_token_refresh.refresh_official_token(env_path=env_file, environ={}) is False


def test_refresh_survives_network_failure(monkeypatch, env_file, environ):
    def boom(*_):
        raise OSError("сеть отвалилась")

    monkeypatch.setattr(vk_token_refresh, "_request_new_token", boom)

    assert vk_token_refresh.refresh_official_token(env_path=env_file, environ=environ) is False
    # Старый токен ещё может быть жив — затирать его отказом нельзя.
    assert environ["VK_TOKEN_OFFICIAL"] == "старый-доступ"


def test_refresh_reports_broken_chain(monkeypatch, env_file, environ, caplog):
    """Использованный refresh — это не сетевой сбой, а «нужен новый вход владельца»."""
    monkeypatch.setattr(
        vk_token_refresh,
        "_request_new_token",
        lambda *_: {"error": "invalid_grant", "error_description": "refresh already used"},
    )

    with caplog.at_level("ERROR"):
        assert vk_token_refresh.refresh_official_token(env_path=env_file, environ=environ) is False

    assert "новый вход" in caplog.text


def test_refresh_keeps_token_in_memory_when_file_is_unwritable(monkeypatch, tmp_path, environ):
    """Записать не вышло — но токен уже выдан, а старый refresh погашен.

    Потерять его молча значит остаться без доступа до вмешательства человека, поэтому в
    память кладём в любом случае."""
    monkeypatch.setattr(
        vk_token_refresh,
        "_request_new_token",
        lambda *_: {"access_token": "новый-доступ", "refresh_token": "новый-refresh"},
    )

    unwritable = tmp_path / "нет-такого-каталога" / ".env"
    assert vk_token_refresh.refresh_official_token(env_path=unwritable, environ=environ)
    assert environ["VK_TOKEN_OFFICIAL"] == "новый-доступ"
    assert environ["VK_TOKEN_OFFICIAL_REFRESH"] == "новый-refresh"


def test_job_refreshes_every_configured_variable(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(
        vk_token_refresh,
        "refresh_official_token",
        lambda variable: asked.append(variable),
    )

    vk_token_refresh.build_token_refresh_job(("VK_TOKEN_OFFICIAL", "VK_TOKEN_OFFICIAL_2"))()

    assert asked == ["VK_TOKEN_OFFICIAL", "VK_TOKEN_OFFICIAL_2"]
