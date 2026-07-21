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
