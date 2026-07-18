"""Видео-источник ежедневного репоста: парсинг video.get (VK) и YouTube-канала
(основной источник — VK жёстко троттлит видео-CDN для датацентр-IP, см. daily_video_repost.py),
выбор непубликовавшегося."""
from unittest.mock import MagicMock, patch

from app.core.video.video_source import (
    fetch_youtube_video_details,
    list_youtube_channel_videos,
    pick_unreposted,
    pick_unreposted_youtube,
    source_video_from_item,
)


def _fake_ydl(extract_info_return):
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = extract_info_return
    return ydl


def _item(video_id=1, owner=-223779047, **extra):
    return {
        "id": video_id,
        "owner_id": owner,
        "title": "Интерстеллар (2014)",
        "description": "Фантастика про космос",
        "duration": 7200,
        "files": {
            "mp4_240": "http://cdn/240.mp4",
            "mp4_480": "http://cdn/480.mp4",
            "mp4_720": "http://cdn/720.mp4",
            "hls": "http://cdn/index.m3u8",
        },
        **extra,
    }


def test_source_video_from_item_parses_fields_and_direct_urls():
    video = source_video_from_item(_item(video_id=42))

    assert video.ref == "-223779047_42"
    assert video.title == "Интерстеллар (2014)"
    assert video.duration_seconds == 7200
    assert video.direct_urls == {
        240: "http://cdn/240.mp4",
        480: "http://cdn/480.mp4",
        720: "http://cdn/720.mp4",
    }  # hls не мешается в mp4-ссылки
    assert video.page_url == "https://vk.com/video-223779047_42"


def test_source_video_from_item_handles_missing_files_and_texts():
    video = source_video_from_item(
        {"id": 7, "owner_id": -1, "title": None, "description": None, "duration": 100}
    )
    assert video.direct_urls == {}
    assert video.title == ""
    assert video.description == ""


def test_pick_unreposted_returns_newest_not_yet_reposted():
    videos = [source_video_from_item(_item(video_id=i)) for i in (30, 20, 10)]

    picked = pick_unreposted(videos, reposted_refs={"-223779047_30"})

    assert picked is not None
    assert picked.video_id == 20  # самый свежий из ещё не публиковавшихся


def test_pick_unreposted_skips_zero_duration_and_returns_none_when_exhausted():
    videos = [
        source_video_from_item(_item(video_id=1, duration=0)),  # трансляция/битое
        source_video_from_item(_item(video_id=2)),
    ]
    assert pick_unreposted(videos, reposted_refs={"-223779047_2"}) is None


def test_list_youtube_channel_videos_parses_flat_entries():
    fake_info = {
        "entries": [
            {"id": "abc123", "title": "Фильм 1"},
            {"id": "def456", "title": "Фильм 2"},
            None,  # yt-dlp иногда отдаёт None для недоступных видео — не должен падать
        ]
    }
    with patch("yt_dlp.YoutubeDL", return_value=_fake_ydl(fake_info)) as ydl_cls:
        videos = list_youtube_channel_videos("https://www.youtube.com/@mmalive1830", count=10)

    assert videos == [
        {"id": "abc123", "title": "Фильм 1", "url": "https://www.youtube.com/watch?v=abc123"},
        {"id": "def456", "title": "Фильм 2", "url": "https://www.youtube.com/watch?v=def456"},
    ]
    called_url = ydl_cls.return_value.extract_info.call_args[0][0]
    assert called_url == "https://www.youtube.com/@mmalive1830/videos"


def test_fetch_youtube_video_details_parses_full_metadata():
    fake_info = {
        "title": "КЗК 2026",
        "description": "Боевик про кадетов",
        "duration": 5400,
    }
    with patch("yt_dlp.YoutubeDL", return_value=_fake_ydl(fake_info)):
        video = fetch_youtube_video_details("abc123")

    assert video.ref == "youtube_abc123"
    assert video.title == "КЗК 2026"
    assert video.description == "Боевик про кадетов"
    assert video.duration_seconds == 5400
    assert video.direct_urls == {}
    assert video.page_url == "https://www.youtube.com/watch?v=abc123"


def test_pick_unreposted_youtube_returns_newest_not_reposted():
    flat_info = {"entries": [{"id": "old"}, {"id": "new"}]}
    details = {"new": {"title": "Новый", "description": "", "duration": 100}}

    def fake_extract_info(url, download=False):
        if url.endswith("/videos"):
            return flat_info
        video_id = url.rsplit("=", 1)[1]
        return details[video_id]

    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = fake_extract_info

    with patch("yt_dlp.YoutubeDL", return_value=ydl):
        video = pick_unreposted_youtube("https://www.youtube.com/@ch", reposted_refs={"youtube_old"})

    assert video is not None
    assert video.ref == "youtube_new"
    assert video.title == "Новый"


def test_pick_unreposted_youtube_returns_none_when_all_reposted():
    flat_info = {"entries": [{"id": "a"}, {"id": "b"}]}
    with patch("yt_dlp.YoutubeDL", return_value=_fake_ydl(flat_info)):
        video = pick_unreposted_youtube(
            "https://www.youtube.com/@ch", reposted_refs={"youtube_a", "youtube_b"}
        )
    assert video is None


def test_pick_unreposted_youtube_skips_video_when_details_fetch_fails():
    flat_info = {"entries": [{"id": "broken"}, {"id": "ok"}]}

    def fake_extract_info(url, download=False):
        if url.endswith("/videos"):
            return flat_info
        if "broken" in url:
            raise RuntimeError("недоступно")
        return {"title": "OK", "description": "", "duration": 60}

    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = fake_extract_info

    with patch("yt_dlp.YoutubeDL", return_value=ydl):
        video = pick_unreposted_youtube("https://www.youtube.com/@ch", reposted_refs=set())

    assert video is not None
    assert video.ref == "youtube_ok"
