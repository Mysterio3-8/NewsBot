"""Telegram только читаем: публикация выключается флагом, чтение остаётся.

ТЗ владельца 2026-09-08: «приоритет сейчас ВК, на ТГ можешь забить, с ТГ только брать
и читать посты, публиковать не надо».

Почему флагом, а не снятием `tg_destination`: адрес канала нужен и чтению, и возврату
публикации одной строкой. Плюс это закрывает поломку с фильмами — аккаунт заливки
перестал быть участником `@kinobestfilmss`, и каждая попытка отдавала
`ChatWriteForbiddenError` (68 раз в журнале, минимум с 31.08).
"""

from __future__ import annotations

from app.core.channel_settings import ChannelSettings


def test_flag_defaults_to_publishing():
    """Каналы, которых требование не касается, ведут себя как раньше."""
    assert ChannelSettings().tg_publish_enabled is True


def test_flag_survives_json_roundtrip():
    settings = ChannelSettings(tg_publish_enabled=False)

    restored = ChannelSettings.from_json(settings.to_json())

    assert restored.tg_publish_enabled is False


def test_default_is_not_written_to_json():
    """Значение по умолчанию не раздувает settings_json у остальных каналов."""
    assert "tg_publish_enabled" not in ChannelSettings().to_json()


def test_both_live_channels_have_telegram_publishing_off():
    """Новости и Кино — оба на VK. Если кто-то вернёт TG, пусть это будет осознанно."""
    from app.seed_channels import DAILY_PLAN, NEWS_ANTIBAN

    assert DAILY_PLAN["tg_publish_enabled"] is False
    assert NEWS_ANTIBAN["tg_publish_enabled"] is False
