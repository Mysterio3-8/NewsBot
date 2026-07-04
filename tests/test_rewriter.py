from unittest.mock import Mock

from app.config.loader import LLMConfig
from app.core.llm.client import LLMClient
from app.core.llm.rewriter import (
    HASHTAGS_INSTRUCTION_OFF,
    HASHTAGS_INSTRUCTION_ON,
    rewrite_post,
)

REAL_CONFIG = LLMConfig(
    provider="groq",
    host="",
    model="test-model",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=0,
)


def test_rewrite_post_uses_style_modifier_file():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: kwargs["STYLE"]
    client.generate.return_value = "  переписанный текст  "

    result = rewrite_post(client, text="исходный текст", source="tg", style="viral", max_length=900)

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

    rewrite_post(client, text="x", source="tg", style="unknown", max_length=900)

    _, render_kwargs = client.render.call_args
    assert render_kwargs["STYLE"] == "unknown"


def test_rewrite_post_passes_max_length_to_real_template_placeholder():
    """Регрессия: prompts/rewrite.txt требует {{MAX_LENGTH}}, но rewrite_post его
    не передавал в render() — реальный вызов падал KeyError на любом посте,
    дошедшем до рерайта (обнаружено при сквозном тесте с реальным LLM 2026-07-02).
    """
    client = LLMClient(REAL_CONFIG)  # реальный render(), не мок — ловит рассинхрон с промптом
    client.load_prompt = Mock(return_value="{{MAX_LENGTH}} знаков")
    client.generate = Mock(return_value="текст")

    rewrite_post(client, text="x", source="tg", style="viral", max_length=900)

    user_prompt = client.generate.call_args.args[1]
    assert "900 знаков" in user_prompt


def test_rewrite_post_defaults_to_no_hashtags_instruction_on_real_template():
    """Регрессия: prompts/rewrite.txt раньше безусловно требовал от LLM 3 хэштега,
    независимо от include_hashtags — из-за этого хэштеги (и слипшаяся с ними строка
    "Источник: ...") просачивались в опубликованный текст даже при include_hashtags=False
    (обнаружено на реальном опубликованном посте 2026-07-04). Реальный render(),
    не мок — ловит рассинхрон плейсхолдеров, если промпт снова поменяют без кода."""
    client = LLMClient(REAL_CONFIG)  # реальный load_prompt/render читают настоящий prompts/rewrite.txt
    client.generate = Mock(return_value="текст")

    rewrite_post(client, text="x", source="tg", style="viral", max_length=900, include_hashtags=False)
    prompt_off = client.generate.call_args.args[1]
    assert HASHTAGS_INSTRUCTION_OFF in prompt_off
    assert "Источник: tg" not in prompt_off


def test_rewrite_post_includes_hashtags_instruction_when_enabled_on_real_template():
    client = LLMClient(REAL_CONFIG)
    client.generate = Mock(return_value="текст")

    rewrite_post(client, text="x", source="tg", style="viral", max_length=900, include_hashtags=True)
    prompt_on = client.generate.call_args.args[1]
    assert HASHTAGS_INSTRUCTION_ON in prompt_on
