from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.publishing.footer import FooterLinks
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.core.publishing.vk_queue_service import PostNotFoundError, publish_queued_post_vk
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_publish_queued_post_vk_marks_published_on_success(tmp_path, caplog):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости",
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=681, error=None)

    with caplog.at_level("INFO", logger="publishing"):
        result = publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    assert result.success is True
    publisher.publish.assert_called_once_with(
        group_id=123, text="Текст новости", image_paths=[]
    )
    assert repo.get_published_network_at(processed.id, "vk") is not None
    assert repo.get_published_network_at(processed.id, "tg") is None
    assert "опубликован в VK" in caplog.text
    assert repo.list_processed_posts(status="published")[0].id == processed.id


def test_publish_queued_post_vk_uses_bracket_link_footer_and_moves_hashtags(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости.\n\n#технологии #apple",
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    footer_links = FooterLinks(label="Подписывайтесь на нас", telegram_url="https://t.me/x")

    publish_queued_post_vk(
        repo,
        publisher,
        post_id=processed.id,
        group_id=123,
        footer_links=footer_links,
        include_hashtags=True,
    )

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == (
        "Текст новости.\n\n"
        "Подписывайтесь на нас: [https://t.me/x|Telegram]\n\n"
        "#технологии #apple"
    )


def test_publish_queued_post_vk_excludes_hashtags_by_default(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости.\n\n#технологии #apple",
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    _, kwargs = publisher.publish.call_args
    assert "#технологии" not in kwargs["text"]
    assert kwargs["text"] == "Текст новости."


def test_publish_queued_post_vk_strips_markdown(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="**Путин** заявил, что *ситуация стабильна*.",
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == "Путин заявил, что ситуация стабильна."
    assert "*" not in kwargs["text"]


def test_publish_queued_post_vk_passes_image_paths_from_db(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="Текст",
        image_paths=["output/images/1/a.jpg"],
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    _, kwargs = publisher.publish.call_args
    assert kwargs["image_paths"] == [Path("output/images/1/a.jpg")]


def test_publish_queued_post_vk_passes_video_path_from_db(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="Текст",
        video_path="output/videos/1/a.mp4",
        status="queued",
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    _, kwargs = publisher.publish.call_args
    assert kwargs["video_path"] == Path("output/videos/1/a.mp4")


def test_publish_queued_post_vk_marks_failed_on_error(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=False, post_id=None, error="сбой")

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    assert repo.list_processed_posts(status="failed")[0].id == processed.id


def test_publish_queued_post_vk_failure_does_not_downgrade_already_published(tmp_path):
    """Регрессия: пост уже опубликован в TG (status='published'), VK падает — статус
    НЕ должен стать 'failed', иначе rate_guard перестанет его учитывать как публикацию."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="published"
    )

    publisher = Mock(spec=VKPublisher)
    publisher.publish.return_value = VKPublishResult(success=False, post_id=None, error="сбой")

    publish_queued_post_vk(repo, publisher, post_id=processed.id, group_id=123)

    assert repo.get_processed_post(processed.id).status == "published"


def test_publish_queued_post_vk_raises_when_missing(tmp_path):
    repo = make_repo(tmp_path)
    publisher = Mock(spec=VKPublisher)

    with pytest.raises(PostNotFoundError):
        publish_queued_post_vk(repo, publisher, post_id=999, group_id=123)
