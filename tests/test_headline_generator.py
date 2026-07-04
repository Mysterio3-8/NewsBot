from unittest.mock import Mock

from app.config.loader import LLMConfig
from app.core.llm.client import LLMClient
from app.core.llm.headline_generator import generate_headlines

REAL_CONFIG = LLMConfig(
    provider="groq",
    host="",
    model="test-model",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=0,
)


def make_mock_client() -> Mock:
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    return client


def test_generate_headlines_parses_json_response():
    client = make_mock_client()
    client.generate.return_value = (
        '{"headlines": ["Заголовок один", "Заголовок два", "Заголовок три"]}'
    )

    headlines = generate_headlines(client, text="новость", style="viral", count=3)

    assert headlines == ["Заголовок один", "Заголовок два", "Заголовок три"]


def test_generate_headlines_strips_markdown_json_fence():
    client = make_mock_client()
    client.generate.return_value = '```json\n{"headlines": ["Заголовок один"]}\n```'

    headlines = generate_headlines(client, text="новость", style="viral", count=3)

    assert headlines == ["Заголовок один"]


def test_generate_headlines_limits_to_count():
    client = make_mock_client()
    client.generate.return_value = '{"headlines": ["A", "B", "C", "D"]}'

    headlines = generate_headlines(client, text="новость", style="viral", count=2)

    assert headlines == ["A", "B"]


def test_generate_headlines_falls_back_to_plain_lines_when_not_json():
    client = make_mock_client()
    client.generate.return_value = "- Заголовок один\n- Заголовок два\n- Заголовок три\n- Лишний"

    headlines = generate_headlines(client, text="новость", style="viral", count=3)

    assert headlines == ["Заголовок один", "Заголовок два", "Заголовок три"]


def test_generate_headlines_ignores_empty_lines_in_fallback():
    client = make_mock_client()
    client.generate.return_value = "Заголовок один\n\n\nЗаголовок два"

    headlines = generate_headlines(client, text="новость", style="viral", count=3)

    assert headlines == ["Заголовок один", "Заголовок два"]


def test_generate_headlines_passes_style_to_real_template_placeholder():
    """Регрессия: prompts/headline.txt требует {{STYLE}}, но generate_headlines его
    не передавал в render() — реальный вызов падал KeyError (обнаружено при
    сквозном тесте с реальным LLM 2026-07-02).
    """
    client = LLMClient(REAL_CONFIG)  # реальный render(), не мок — ловит рассинхрон с промптом
    client.load_prompt = Mock(return_value="стиль: {{STYLE}}")
    client.generate = Mock(return_value='{"headlines": ["ok"]}')

    generate_headlines(client, text="новость", style="viral", count=3)

    user_prompt = client.generate.call_args.args[1]
    assert "стиль: viral" in user_prompt
