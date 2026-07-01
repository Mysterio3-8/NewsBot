from unittest.mock import Mock

from app.core.llm.client import LLMClient
from app.core.llm.rewriter import rewrite_post


def test_rewrite_post_uses_style_modifier_file():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: kwargs["STYLE"]
    client.generate.return_value = "  переписанный текст  "

    result = rewrite_post(client, text="исходный текст", source="tg", style="viral")

    assert result == "переписанный текст"
    client.render.assert_called_once()
    _, render_kwargs = client.render.call_args
    assert render_kwargs["STYLE"] == "<style_viral>"


def test_rewrite_post_falls_back_to_raw_style_when_file_missing():
    client = Mock(spec=LLMClient)

    def load_prompt(name: str) -> str:
        if name == "style_unknown":
            raise FileNotFoundError(name)
        return f"<{name}>"

    client.load_prompt.side_effect = load_prompt
    client.render.side_effect = lambda template, **kwargs: kwargs["STYLE"]
    client.generate.return_value = "текст"

    rewrite_post(client, text="x", source="tg", style="unknown")

    _, render_kwargs = client.render.call_args
    assert render_kwargs["STYLE"] == "unknown"
