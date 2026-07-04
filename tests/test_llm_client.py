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

GEMINI_CONFIG = LLMConfig(
    provider="gemini",
    host="",
    model="gemini-2.0-flash",
    api_key_env="GEMINI_API_KEY",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=1,
)

GROQ_CONFIG = LLMConfig(
    provider="groq",
    host="",
    model="llama-3.3-70b-versatile",
    api_key_env="GROQ_API_KEY",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=1,
)

OPENROUTER_CONFIG = LLMConfig(
    provider="openrouter",
    host="",
    model="meta-llama/llama-3.3-70b-instruct:free",
    api_key_env="OPENROUTER_API_KEY",
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


def test_gemini_is_running_false_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = LLMClient(GEMINI_CONFIG)
    assert client.is_running() is False


def test_gemini_is_running_true_when_key_present_and_api_responds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_running() is True


def test_gemini_is_model_downloaded_checks_model_list(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_model_downloaded() is True


def test_gemini_is_model_downloaded_false_when_model_missing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "models/gemini-1.5-flash"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_model_downloaded() is False


def test_gemini_generate_returns_content_on_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "готовый текст"}]}}]
    }

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response) as mock_post,
    ):
        result = client.generate("system", "user")

    assert result == "готовый текст"
    called_url = mock_post.call_args.args[0]
    assert "gemini-2.0-flash:generateContent" in called_url
    assert mock_post.call_args.kwargs["params"] == {"key": "test-key"}


def test_gemini_generate_raises_immediately_when_key_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = LLMClient(GEMINI_CONFIG)
    with pytest.raises(LLMUnavailableError):
        client.generate("system", "user")


def test_gemini_requests_use_proxy_when_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "socks5h://user:pass@proxy.example:1080")
    client = LLMClient(GEMINI_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}

    with patch("app.core.llm.client.requests.get", return_value=response) as mock_get:
        client.is_running()

    expected_proxies = {
        "https": "socks5h://user:pass@proxy.example:1080",
        "http": "socks5h://user:pass@proxy.example:1080",
    }
    assert mock_get.call_args.kwargs["proxies"] == expected_proxies


def test_gemini_requests_have_no_proxy_when_not_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)
    client = LLMClient(GEMINI_CONFIG)
    response = Mock()
    response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}

    with patch("app.core.llm.client.requests.get", return_value=response) as mock_get:
        client.is_running()

    assert mock_get.call_args.kwargs["proxies"] is None


def test_groq_requests_ignore_proxy_even_when_configured(monkeypatch):
    """Groq работает из РФ напрямую — LLM_PROXY_URL (нужен только Gemini) не должен
    применяться к нему, иначе лишняя точка отказа на нестабильном прокси."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "socks5h://user:pass@proxy.example:1080")
    client = LLMClient(GROQ_CONFIG)
    response = Mock()
    response.json.return_value = {"data": [{"id": "llama-3.3-70b-versatile"}]}

    with patch("app.core.llm.client.requests.get", return_value=response) as mock_get:
        client.is_running()

    assert mock_get.call_args.kwargs["proxies"] is None


def test_gemini_throttle_sleeps_when_called_too_soon(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
    }

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response),
        patch("app.core.llm.client.time.monotonic", side_effect=[100.0, 101.0, 101.0]),
        patch("app.core.llm.client.time.sleep") as mock_sleep,
    ):
        client.generate("system", "user")
        client.generate("system", "user")

    mock_sleep.assert_called_once()
    slept_seconds = mock_sleep.call_args.args[0]
    assert slept_seconds == pytest.approx(3.5)  # 4.5s минимум - 1.0s, прошедшая между вызовами


def test_gemini_throttle_no_sleep_on_first_call(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = LLMClient(GEMINI_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {"models": [{"name": "models/gemini-2.0-flash"}]}
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
    }

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response),
        patch("app.core.llm.client.time.sleep") as mock_sleep,
    ):
        client.generate("system", "user")

    mock_sleep.assert_not_called()


def test_groq_is_running_false_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client = LLMClient(GROQ_CONFIG)
    assert client.is_running() is False


def test_groq_is_model_downloaded_checks_model_list(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_CONFIG)
    response = Mock()
    response.json.return_value = {"data": [{"id": "llama-3.3-70b-versatile"}]}
    with patch("app.core.llm.client.requests.get", return_value=response) as mock_get:
        assert client.is_model_downloaded() is True

    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}


def test_groq_is_model_downloaded_false_when_model_missing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_CONFIG)
    response = Mock()
    response.json.return_value = {"data": [{"id": "llama-3.1-8b-instant"}]}
    with patch("app.core.llm.client.requests.get", return_value=response):
        assert client.is_model_downloaded() is False


def test_groq_generate_returns_content_on_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {"data": [{"id": "llama-3.3-70b-versatile"}]}
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {
        "choices": [{"message": {"content": "готовый текст"}}]
    }

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response) as mock_post,
    ):
        result = client.generate("system", "user")

    assert result == "готовый текст"
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert "chat/completions" in mock_post.call_args.args[0]


def test_groq_generate_raises_immediately_when_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client = LLMClient(GROQ_CONFIG)
    with pytest.raises(LLMUnavailableError):
        client.generate("system", "user")


GROQ_VISION_CONFIG = LLMConfig(
    provider="groq",
    host="",
    model="llama-3.3-70b-versatile",
    api_key_env="GROQ_API_KEY",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=1,
    vision_model="meta-llama/llama-4-scout-17b-16e-instruct",
)


def test_generate_vision_raises_when_provider_not_openai_compatible(tmp_path):
    client = LLMClient(TEST_CONFIG)  # ollama
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-image-bytes")
    with pytest.raises(LLMUnavailableError):
        client.generate_vision("вопрос", image_path)


def test_generate_vision_raises_when_vision_model_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_CONFIG)  # vision_model="" по умолчанию
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-image-bytes")
    with pytest.raises(LLMUnavailableError):
        client.generate_vision("вопрос", image_path)


def test_generate_vision_returns_content_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_VISION_CONFIG)
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "ДА"}}]}

    with patch("app.core.llm.client.requests.post", return_value=response) as mock_post:
        result = client.generate_vision("вопрос", image_path)

    assert result == "ДА"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "meta-llama/llama-4-scout-17b-16e-instruct"
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "вопрос"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_generate_vision_raises_on_request_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = LLMClient(GROQ_VISION_CONFIG)
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    with patch("app.core.llm.client.requests.post", side_effect=requests.ConnectionError("сбой")):
        with pytest.raises(LLMUnavailableError):
            client.generate_vision("вопрос", image_path)


def test_openrouter_is_running_false_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = LLMClient(OPENROUTER_CONFIG)
    assert client.is_running() is False


def test_openrouter_is_model_downloaded_checks_model_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = LLMClient(OPENROUTER_CONFIG)
    response = Mock()
    response.json.return_value = {
        "data": [{"id": "meta-llama/llama-3.3-70b-instruct:free"}]
    }
    with patch("app.core.llm.client.requests.get", return_value=response) as mock_get:
        assert client.is_model_downloaded() is True

    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert "openrouter.ai" in mock_get.call_args.args[0]


def test_openrouter_generate_returns_content_and_hits_openrouter_base(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = LLMClient(OPENROUTER_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {
        "data": [{"id": "meta-llama/llama-3.3-70b-instruct:free"}]
    }
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {"choices": [{"message": {"content": "текст"}}]}

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response) as mock_post,
        patch("app.core.llm.client.time.sleep"),
    ):
        result = client.generate("system", "user")

    assert result == "текст"
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert mock_post.call_args.args[0].startswith("https://openrouter.ai/api/v1")


def test_openrouter_requests_ignore_proxy_even_when_configured(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "socks5h://127.0.0.1:1080")
    client = LLMClient(OPENROUTER_CONFIG)
    list_response = Mock()
    list_response.json.return_value = {
        "data": [{"id": "meta-llama/llama-3.3-70b-instruct:free"}]
    }
    generate_response = Mock()
    generate_response.raise_for_status = Mock()
    generate_response.json.return_value = {"choices": [{"message": {"content": "x"}}]}

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", return_value=generate_response) as mock_post,
        patch("app.core.llm.client.time.sleep"),
    ):
        client.generate("system", "user")

    assert mock_post.call_args.kwargs["proxies"] is None


def test_openrouter_generate_raises_immediately_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = LLMClient(OPENROUTER_CONFIG)
    with pytest.raises(LLMUnavailableError):
        client.generate("system", "user")


def _http_429() -> requests.HTTPError:
    response = Mock()
    response.headers = {}
    response.json.return_value = {}
    error = requests.HTTPError("429 Too Many Requests")
    error.response = response
    return error


def test_generate_falls_back_to_next_model_when_first_is_rate_limited(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = LLMConfig(
        provider="openrouter", host="", model="model-a:free",
        api_key_env="OPENROUTER_API_KEY", temperature=0.7, top_p=0.9,
        timeout_seconds=5, retries=0, fallback_models=["model-b:free"],
    )
    client = LLMClient(config)
    list_response = Mock()
    list_response.json.return_value = {"data": [{"id": "model-a:free"}]}

    ok_response = Mock()
    ok_response.raise_for_status = Mock()
    ok_response.json.return_value = {"choices": [{"message": {"content": "со второй модели"}}]}

    def post_side_effect(url, **kwargs):
        if kwargs["json"]["model"] == "model-a:free":
            raise _http_429()
        return ok_response

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", side_effect=post_side_effect) as mock_post,
        patch("app.core.llm.client.time.sleep"),
    ):
        result = client.generate("system", "user")

    assert result == "со второй модели"
    used_models = [call.kwargs["json"]["model"] for call in mock_post.call_args_list]
    assert used_models == ["model-a:free", "model-b:free"]


def test_generate_raises_when_all_models_rate_limited(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = LLMConfig(
        provider="openrouter", host="", model="model-a:free",
        api_key_env="OPENROUTER_API_KEY", temperature=0.7, top_p=0.9,
        timeout_seconds=5, retries=0, fallback_models=["model-b:free"],
    )
    client = LLMClient(config)
    list_response = Mock()
    list_response.json.return_value = {"data": [{"id": "model-a:free"}]}

    with (
        patch("app.core.llm.client.requests.get", return_value=list_response),
        patch("app.core.llm.client.requests.post", side_effect=_http_429()),
        patch("app.core.llm.client.time.sleep"),
    ):
        with pytest.raises(LLMUnavailableError):
            client.generate("system", "user")


def test_models_to_try_dedupes_and_preserves_order():
    config = LLMConfig(
        provider="groq", host="", model="primary", api_key_env="GROQ_API_KEY",
        temperature=0.7, top_p=0.9, timeout_seconds=5, retries=0,
        fallback_models=["primary", "second", "third"],
    )
    client = LLMClient(config)
    assert client._models_to_try() == ["primary", "second", "third"]
