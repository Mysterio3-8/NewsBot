"""Единый пульт софтов: чистые функции реестра/рендера/тумблера (без aiogram)."""
from __future__ import annotations

import app.control_bot as bot


class FakeController:
    """Заглушка ServiceController/ProcessController: общий интерфейс is_running/start/stop."""

    def __init__(self, running: bool = False) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        self._running = True
        return True

    def stop(self) -> bool:
        self._running = False
        return True


def test_build_software_registry_has_three_softwares():
    softwares = bot.build_software_registry(FakeController(), FakeController(), None)
    assert [s.key for s in softwares] == ["news", "nature", "shorts"]


def test_render_software_panel_shows_each_state():
    softwares = bot.build_software_registry(
        FakeController(running=True), FakeController(running=False), None
    )
    text = bot.render_software_panel(softwares)
    assert "🟢 📰 Автопостинг новостей — запущен" in text
    assert "🔴 🌿 VK Nature — остановлен" in text
    assert "⛔ 🎬 Shorts — не настроен" in text


def test_toggle_software_starts_stopped():
    news = FakeController(running=False)
    softwares = bot.build_software_registry(news, None, None)
    result = bot.toggle_software(softwares, "news")
    assert news.is_running() is True
    assert "запущен" in result


def test_toggle_software_stops_running():
    news = FakeController(running=True)
    softwares = bot.build_software_registry(news, None, None)
    result = bot.toggle_software(softwares, "news")
    assert news.is_running() is False
    assert "остановлен" in result


def test_toggle_software_not_configured():
    softwares = bot.build_software_registry(FakeController(), None, None)
    assert "не настроен" in bot.toggle_software(softwares, "nature")


def test_toggle_software_unknown_key():
    softwares = bot.build_software_registry(FakeController(), None, None)
    assert bot.toggle_software(softwares, "ghost") == "Неизвестный софт."


def test_software_rows_maps_running_and_configured():
    softwares = bot.build_software_registry(
        FakeController(running=True), None, FakeController(running=False)
    )
    rows = {r.key: r for r in bot.software_rows(softwares)}
    assert rows["news"].running is True and rows["news"].configured is True
    assert rows["nature"].running is False and rows["nature"].configured is False
    assert rows["shorts"].running is False and rows["shorts"].configured is True
