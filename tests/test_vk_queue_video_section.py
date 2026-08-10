"""Пост с видео при video_as_post=False уходит в раздел, а не на стену (ТЗ 2026-08-10)."""
from pathlib import Path
from unittest.mock import Mock

from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "queue.db")
    init_db(engine)
    return Repository(engine)


def _queued_post(repo, tmp_path, *, with_video: bool):
    source = repo.create_source(type="tg", name="Источник", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    video_path = None
    if with_video:
        video_file = tmp_path / "clip.mp4"
        video_file.write_bytes(b"x")
        video_path = str(video_file)
    return repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Дроны атаковали Ростов",
        rewritten_text="Текст новости",
        video_path=video_path,
        status="queued",
    )


def _publisher() -> Mock:
    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    publisher.publish_video_only.return_value = VKPublishResult(
        success=True, post_id=None, error=None, attachment="video-1_1"
    )
    return publisher


def test_video_post_goes_to_the_video_section(tmp_path):
    repo = _repo(tmp_path)
    processed = _queued_post(repo, tmp_path, with_video=True)
    publisher = _publisher()

    result = publish_queued_post_vk(
        repo, publisher, post_id=processed.id, group_id=123, video_as_post=False
    )

    assert result.success is True
    publisher.publish.assert_not_called()
    publisher.publish_video_only.assert_called_once()
    assert repo.get_published_network_at(processed.id, "vk") is not None


def test_seo_description_is_used_for_the_video(tmp_path):
    repo = _repo(tmp_path)
    processed = _queued_post(repo, tmp_path, with_video=True)
    publisher = _publisher()

    publish_queued_post_vk(
        repo, publisher, post_id=processed.id, group_id=123,
        video_as_post=False, video_description="большое SEO-описание",
    )

    _, kwargs = publisher.publish_video_only.call_args
    assert kwargs["description"] == "большое SEO-описание"
    assert kwargs["title"] == "Дроны атаковали Ростов"


def test_post_without_video_still_goes_to_the_wall(tmp_path):
    """Фото-посты флаг не трогает — иначе Новости остались бы без ленты."""
    repo = _repo(tmp_path)
    processed = _queued_post(repo, tmp_path, with_video=False)
    publisher = _publisher()

    publish_queued_post_vk(
        repo, publisher, post_id=processed.id, group_id=123, video_as_post=False
    )

    publisher.publish.assert_called_once()
    publisher.publish_video_only.assert_not_called()


def test_video_post_goes_to_the_wall_when_flag_is_on(tmp_path):
    repo = _repo(tmp_path)
    processed = _queued_post(repo, tmp_path, with_video=True)
    publisher = _publisher()

    publish_queued_post_vk(
        repo, publisher, post_id=processed.id, group_id=123, video_as_post=True
    )

    publisher.publish.assert_called_once()
    publisher.publish_video_only.assert_not_called()


def test_unmeasurable_file_is_treated_as_plain_video(tmp_path):
    """ffprobe недоступен/файла нет → обычная видеозапись, а не клип: ошибиться
    в эту сторону дёшево, в обратную — нет."""
    from app.core.publishing.vk_queue_service import _looks_like_clip

    assert _looks_like_clip(Path("нет-такого-файла.mp4")) is False
