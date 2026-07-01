from unittest.mock import Mock

import pytest

from app.core.llm.classifier import ClassificationError, classify_post
from app.core.llm.client import LLMClient

VALID_JSON = """{
  "is_news": true,
  "category": "политика",
  "score": 87,
  "reasons": ["высокая новостная ценность"],
  "reject_reason": null
}"""


def make_client(generate_side_effect) -> LLMClient:
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.side_effect = generate_side_effect
    return client


def test_classify_post_parses_valid_json():
    client = make_client([VALID_JSON])

    result = classify_post(client, text="новость", source="tg", keywords=["Россия"])

    assert result.is_news is True
    assert result.category == "политика"
    assert result.score == 87
    assert result.reject_reason is None


def test_classify_post_extracts_json_from_markdown_fence():
    fenced = f"```json\n{VALID_JSON}\n```"
    client = make_client([fenced])

    result = classify_post(client, text="новость", source="tg", keywords=[])

    assert result.score == 87


def test_classify_post_retries_once_on_invalid_json():
    client = make_client(["не json", VALID_JSON])

    result = classify_post(client, text="новость", source="tg", keywords=[])

    assert result.score == 87
    assert client.generate.call_count == 2


def test_classify_post_raises_after_two_invalid_attempts():
    client = make_client(["не json", "тоже не json"])

    with pytest.raises(ClassificationError):
        classify_post(client, text="новость", source="tg", keywords=[])
