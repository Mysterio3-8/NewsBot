"""Видео-источник ежедневного репоста: парсинг video.get и выбор непубликовавшегося."""
from app.core.video.video_source import pick_unreposted, source_video_from_item


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
