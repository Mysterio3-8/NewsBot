from unittest.mock import Mock

from app.core.images.watermark_detector import locate_foreign_watermark
from app.core.llm.client import LLMClient, LLMUnavailableError


def test_locate_foreign_watermark_returns_empty_set_when_none_found(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "нет"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) == set()


def test_locate_foreign_watermark_returns_top_region(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "верх"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) == {"top"}


def test_locate_foreign_watermark_returns_bottom_region(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "низ"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) == {"bottom"}


def test_locate_foreign_watermark_returns_both_regions(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "верх,низ"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) == {"top", "bottom"}


def test_locate_foreign_watermark_returns_none_when_not_removable(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "не убрать"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) is None


def test_locate_foreign_watermark_treats_unexpected_response_as_not_removable(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "что-то непонятное"
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) is None


def test_locate_foreign_watermark_fails_closed_when_vision_unavailable(tmp_path):
    """FAIL-CLOSED (требование пользователя 2026-07-11): vision недоступен → не знаем,
    есть ли чужой знак → фото НЕ используем (None → сток), а не пропускаем непроверенным."""
    client = Mock(spec=LLMClient)
    client.generate_vision.side_effect = LLMUnavailableError("vision недоступна")
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) is None


def test_locate_foreign_watermark_fails_closed_on_unexpected_error(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.side_effect = RuntimeError("что-то пошло не так")
    image_path = tmp_path / "photo.jpg"

    assert locate_foreign_watermark(client, image_path) is None
