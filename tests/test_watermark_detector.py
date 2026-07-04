from unittest.mock import Mock

from app.core.images.watermark_detector import detect_foreign_watermark
from app.core.llm.client import LLMClient, LLMUnavailableError


def test_detect_foreign_watermark_true_when_model_says_yes(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "ДА"
    image_path = tmp_path / "photo.jpg"

    assert detect_foreign_watermark(client, image_path) is True


def test_detect_foreign_watermark_false_when_model_says_no(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.return_value = "НЕТ"
    image_path = tmp_path / "photo.jpg"

    assert detect_foreign_watermark(client, image_path) is False


def test_detect_foreign_watermark_fails_open_when_vision_unavailable(tmp_path):
    """Vision не настроен/сбой сети — не блокируем публикацию фото из-за этого,
    считаем, что водяного знака нет (fail-open)."""
    client = Mock(spec=LLMClient)
    client.generate_vision.side_effect = LLMUnavailableError("vision недоступна")
    image_path = tmp_path / "photo.jpg"

    assert detect_foreign_watermark(client, image_path) is False


def test_detect_foreign_watermark_fails_open_on_unexpected_error(tmp_path):
    client = Mock(spec=LLMClient)
    client.generate_vision.side_effect = RuntimeError("что-то пошло не так")
    image_path = tmp_path / "photo.jpg"

    assert detect_foreign_watermark(client, image_path) is False
