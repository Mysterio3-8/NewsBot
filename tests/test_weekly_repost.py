from unittest.mock import Mock

from app.core.publishing.weekly_repost import pick_best_post


def _channel():
    return Mock(id=2, vk_destination="240120678", name="Кино")


def test_pick_best_post_picks_highest_views_plus_likes():
    repo = Mock()
    p1, p2, p3 = Mock(vk_post_id=101), Mock(vk_post_id=102), Mock(vk_post_id=103)
    repo.list_channel_posts_published_to_vk_since.return_value = [p1, p2, p3]
    vk = Mock()
    vk.fetch_engagement.return_value = {101: 5, 102: 50, 103: 10}  # 102 — лучший

    assert pick_best_post(repo, vk, _channel()) is p2


def test_pick_best_post_returns_none_without_candidates():
    repo = Mock()
    repo.list_channel_posts_published_to_vk_since.return_value = []

    assert pick_best_post(repo, Mock(), _channel()) is None


def test_pick_best_post_defaults_missing_engagement_to_zero():
    """Пост без данных вовлечённости (getById не вернул) не должен побеждать пост со
    статистикой."""
    repo = Mock()
    p1, p2 = Mock(vk_post_id=201), Mock(vk_post_id=202)
    repo.list_channel_posts_published_to_vk_since.return_value = [p1, p2]
    vk = Mock()
    vk.fetch_engagement.return_value = {202: 3}  # для 201 данных нет → 0

    assert pick_best_post(repo, vk, _channel()) is p2
