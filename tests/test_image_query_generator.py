from unittest.mock import Mock

from app.core.llm.client import LLMClient
from app.core.llm.image_query_generator import generate_image_query


def test_generate_image_query_strips_whitespace():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.return_value = "  president press conference  \n"

    result = generate_image_query(client, text="новость")

    assert result == "president press conference"
