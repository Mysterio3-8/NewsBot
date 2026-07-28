"""Загрузка видео в VK идёт потоком, а не через чтение файла в память.

Регрессия на инцидент 2026-07-27: `requests.post(files={...})` строил multipart-тело
целиком в RAM, фильм на 655 МБ ронял процесс по OOM (`killed status=9`) ещё до отметки
«опубликовано», из-за чего следующий цикл качал тот же фильм заново — 7 раз за ночь.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from requests_toolbelt.multipart.encoder import MultipartEncoder

from app.core.publishing.vk_publisher import VIDEO_UPLOAD_TIMEOUT_SECONDS, _post_video_file


def test_upload_streams_body_instead_of_loading_file(tmp_path):
    video = tmp_path / "film.mp4"
    video.write_bytes(b"x" * 4096)

    with patch("app.core.publishing.vk_publisher.requests.post") as post:
        post.return_value = MagicMock(raise_for_status=MagicMock())
        _post_video_file("http://upload", video)

    kwargs = post.call_args.kwargs
    # files= означало бы чтение файла в память целиком — именно это и роняло процесс.
    assert "files" not in kwargs
    assert isinstance(kwargs["data"], MultipartEncoder)
    assert kwargs["headers"]["Content-Type"].startswith("multipart/form-data")
    assert kwargs["timeout"] == VIDEO_UPLOAD_TIMEOUT_SECONDS


def test_upload_raises_on_http_error(tmp_path):
    video = tmp_path / "film.mp4"
    video.write_bytes(b"x")
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("500")

    with patch("app.core.publishing.vk_publisher.requests.post", return_value=response):
        try:
            _post_video_file("http://upload", video)
        except RuntimeError as error:
            assert "500" in str(error)
        else:
            raise AssertionError("ошибка загрузки должна подниматься наверх")


def test_encoder_body_is_lazy_not_prebuilt(tmp_path):
    """MultipartEncoder знает размер тела, но не держит его в памяти — проверяем, что
    объём файла не превращается в объём буфера."""
    video = tmp_path / "big.mp4"
    video.write_bytes(b"x" * (2 * 1024 * 1024))

    with open(video, "rb") as file:
        encoder = MultipartEncoder(fields={"video_file": (video.name, file, "video/mp4")})
        assert encoder.len > 2 * 1024 * 1024  # знает полный размер
        head = encoder.read(64)  # читает по требованию, а не всё сразу

    assert len(head) == 64
