from unittest.mock import Mock

from app.core.llm.client import LLMClient
from app.core.llm.headline_generator import generate_headlines


def test_generate_headlines_splits_and_limits_variants():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.return_value = "- Заголовок один\n- Заголовок два\n- Заголовок три\n- Лишний"

    headlines = generate_headlines(client, text="новость", count=3)

    assert headlines == ["Заголовок один", "Заголовок два", "Заголовок три"]


def test_generate_headlines_ignores_empty_lines():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.return_value = "Заголовок один\n\n\nЗаголовок два"

    headlines = generate_headlines(client, text="новость", count=3)

    assert headlines == ["Заголовок один", "Заголовок два"]
