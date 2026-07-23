"""YouTubePublisher: сборка запроса, best-effort при сбое, фабрика из .env."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.publishing.youtube_publisher import YouTubeCredentials, YouTubePublisher
from app.factories import build_youtube_publisher

CREDS = YouTubeCredentials(client_id="cid", client_secret="secret", refresh_token="rt")


def test_upload_returns_video_id_and_sends_expected_request(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    publisher = YouTubePublisher(CREDS, privacy="public")

    insert = MagicMock()
    insert.return_value.execute.return_value = {"id": "abc123"}
    fake_client = MagicMock()
    fake_client.videos.return_value.insert = insert

    with patch.object(publisher, "_build_client", return_value=fake_client), \
         patch.object(publisher, "_build_media", return_value="MEDIA"):
        video_id = publisher.upload(video, title="T", description="D", is_short=True)

    assert video_id == "abc123"
    kwargs = insert.call_args.kwargs
    assert kwargs["body"]["status"]["privacyStatus"] == "public"
    assert kwargs["body"]["snippet"]["title"] == "T"
    assert kwargs["media_body"] == "MEDIA"


def test_upload_is_best_effort_on_failure(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    publisher = YouTubePublisher(CREDS)

    with patch.object(publisher, "_build_client", side_effect=RuntimeError("api down")):
        assert publisher.upload(video, title="T", description="D", is_short=False) is None


def test_factory_returns_none_without_full_credentials():
    with patch.dict("os.environ", {"YT_UPLOAD_CLIENT_ID": "cid"}, clear=True):
        assert build_youtube_publisher() is None


def test_factory_builds_publisher_with_all_env():
    env = {
        "YT_UPLOAD_CLIENT_ID": "cid",
        "YT_UPLOAD_CLIENT_SECRET": "secret",
        "YT_UPLOAD_REFRESH_TOKEN": "rt",
        "YT_UPLOAD_PRIVACY": "unlisted",
    }
    with patch.dict("os.environ", env, clear=True):
        publisher = build_youtube_publisher()

    assert isinstance(publisher, YouTubePublisher)
    assert publisher._privacy == "unlisted"
