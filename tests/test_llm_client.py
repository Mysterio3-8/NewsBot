from unittest.mock import Mock, patch

import pytest
import requests

from app.config.loader import LLMConfig
from app.core.llm.client import LLMClient, LLMUnavailableError

TEST_CONFIG = LLMConfig(
    provider="ollama",
    host="http://localhost:11434",
    model="qwen2.5:7b",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=1,
)


def test_is_running_true_when_ollama_responds():
    client = LLMClient(TEST_CONFIG)
    with patch("app.core.llm.client.requests.get", return_value=Mock(ok=True)):
        assert client.is_running() is True


def test_is_running_false_when_connection_fails():
    client = LLMClient(TEST_CONFIG)
    with patch("app.core.llm.client.requests.get", side_effect=requests.ConnectionError):
        assert client.is_running() is False


def test_is_model_downloaded_checks_tags_list():
    client = LLMClient(TEST_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_model_downloaded() is True


def test_is_model_downloaded_false_when_model_missing():
    client = LLMClient(TEST_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "llama3.1:8b"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_model_downloaded() is False


def test_render_substitutes_placeholders():
    client = LLMClient(TEST_CONFIG)
    result = client.render("Привет, {{NAME}}! Тема: {{TOPIC}}", NAME="мир", TOPIC="тест")
    assert result == "Привет, мир! Тема: тест"


def test_render_raises_on_missing_placeholder():
    client = LLMClient(TEST_CONFIG)
    with pytest.raises(KeyError):
        client.render("{{MISSING}}")


def test_generate_returns_content_on_success():
    client = LLMClient(TEST_CONFIG)
    chat_response = Mock()
    chat_response.json.return_value = {"message": {"content": "готовый текст"}}
    chat_response.raise_for_status = Mock()

    with (
        patch("app.core.llm.client.requests.get", return_value=Mock(ok=True)),
        patch("app.core.llm.client.requests.post", return_value=chat_response),
    ):
        result = client.generate("system", "user")

    assert result == "готовый текст"


def test_generate_raises_after_exhausting_retries():
    client = LLMClient(TEST_CONFIG)
    with (
        patch("app.core.llm.client.requests.get", return_value=Mock(ok=True)),
        patch("app.core.llm.client.requests.post", side_effect=requests.Timeout),
    ):
        with pytest.raises(LLMUnavailableError):
            client.generate("system", "user")


def test_generate_raises_immediately_when_ollama_not_running():
    client = LLMClient(TEST_CONFIG)
    with patch("app.core.llm.client.requests.get", side_effect=requests.ConnectionError):
        with pytest.raises(LLMUnavailableError):
            client.generate("system", "user")
