"""Опции yt-dlp: опциональный cookie-файл (YT_COOKIES_FILE)."""
from __future__ import annotations

from unittest.mock import patch

from app.core.video.video_source import ytdlp_options


def test_no_cookie_file_configured_means_anonymous_access():
    with patch.dict("os.environ", {}, clear=True):
        options = ytdlp_options()

    assert "cookiefile" not in options
    assert options["quiet"] is True


def test_existing_cookie_file_is_passed_to_ytdlp(tmp_path):
    cookies = tmp_path / "youtube.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with patch.dict("os.environ", {"YT_COOKIES_FILE": str(cookies)}, clear=True):
        options = ytdlp_options()

    assert options["cookiefile"] == str(cookies)


def test_missing_cookie_file_is_ignored_not_fatal(tmp_path):
    """Пропавший файл не должен ронять скачивание — ходим анонимно, как и раньше."""
    with patch.dict("os.environ", {"YT_COOKIES_FILE": str(tmp_path / "nope.txt")}, clear=True):
        options = ytdlp_options()

    assert "cookiefile" not in options


def test_overrides_win_over_defaults():
    with patch.dict("os.environ", {}, clear=True):
        options = ytdlp_options(skip_download=True, quiet=False)

    assert options["skip_download"] is True
    assert options["quiet"] is False


def test_pot_script_is_wired_when_present(tmp_path, monkeypatch):
    """Без PO-token YouTube отдаёт одни раскадровки — проверено живыми вызовами
    2026-08-14. Скрипт есть → опция выставлена."""
    from app.core.video.video_source import POT_SCRIPT_ENV, ytdlp_options

    script = tmp_path / "generate_once.js"
    script.write_text("// заглушка", encoding="utf-8")
    monkeypatch.setenv(POT_SCRIPT_ENV, str(script))

    options = ytdlp_options()

    assert options["extractor_args"]["youtubepot-bgutilscript"]["script_path"] == [str(script)]
    # n-challenge решается внешним движком; yt-dlp по умолчанию ищет только Deno.
    assert "node" in options["js_runtimes"]


def test_missing_pot_script_does_not_break_options(tmp_path, monkeypatch):
    """На дев-машине провайдера нет — опции просто не появляются, код не падает."""
    from app.core.video.video_source import POT_SCRIPT_ENV, ytdlp_options

    monkeypatch.setenv(POT_SCRIPT_ENV, str(tmp_path / "нет-такого.js"))

    assert "extractor_args" not in ytdlp_options()


def test_master_cookies_are_restored_before_each_call(tmp_path, monkeypatch):
    """yt-dlp пишет свою банку обратно в cookiefile и так съедает эталон: на проде
    17 879 байт превратились в 3 654 за одну сессию. Эталон обязан пережить это."""
    from app.core.video.video_source import COOKIES_MASTER_ENV, ytdlp_options

    master = tmp_path / "эталон.txt"
    master.write_text("# Netscape HTTP Cookie File\nполный набор\n", encoding="utf-8")
    working = tmp_path / "рабочая.txt"
    working.write_text("огрызок\n", encoding="utf-8")
    monkeypatch.setenv("YT_COOKIES_FILE", str(working))
    monkeypatch.setenv(COOKIES_MASTER_ENV, str(master))

    assert ytdlp_options()["cookiefile"] == str(working)
    assert working.read_text(encoding="utf-8") == master.read_text(encoding="utf-8")

    # yt-dlp испортил рабочую копию — следующий вызов восстанавливает её из эталона
    working.write_text("испорчено\n", encoding="utf-8")
    ytdlp_options()
    assert "полный набор" in working.read_text(encoding="utf-8")


def test_without_master_working_file_is_used_as_is(tmp_path, monkeypatch):
    from app.core.video.video_source import COOKIES_MASTER_ENV, ytdlp_options

    working = tmp_path / "куки.txt"
    working.write_text("что есть\n", encoding="utf-8")
    monkeypatch.setenv("YT_COOKIES_FILE", str(working))
    monkeypatch.delenv(COOKIES_MASTER_ENV, raising=False)

    assert ytdlp_options()["cookiefile"] == str(working)
    assert working.read_text(encoding="utf-8") == "что есть\n"
