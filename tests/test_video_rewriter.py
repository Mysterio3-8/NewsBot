"""AI-рерайт названия/описания видео. Правило проекта: мокаем только generate,
НЕ render и НЕ load_prompt — реальный рендер реального промпта ловит рассинхрон
плейсхолдеров код/промпт (грабля, дважды случавшаяся на других промптах)."""
from unittest.mock import Mock

from app.config.loader import LLMConfig
from app.core.llm.client import LLMClient
from app.core.llm.video_rewriter import rewrite_video_texts

REAL_CONFIG = LLMConfig(
    provider="groq",
    host="",
    model="test-model",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=0,
)


def _client_with_generate(side_effect) -> LLMClient:
    client = LLMClient(REAL_CONFIG)
    client.generate = Mock(side_effect=side_effect)
    return client


def test_rewrites_both_title_and_description_with_real_prompts():
    client = _client_with_generate(["Новое название", "Новое описание"])

    title, description = rewrite_video_texts(
        client, title="Старое название", description="Старое описание"
    )

    assert title == "Новое название"
    assert description == "Новое описание"
    # Реальные промпты реально отрендерились — исходные тексты дошли до LLM.
    first_prompt = client.generate.call_args_list[0].args[1]
    second_prompt = client.generate.call_args_list[1].args[1]
    assert "Старое название" in first_prompt
    assert "Старое описание" in second_prompt


def test_no_ai_when_title_and_description_missing():
    """Нельзя понять, что за фильм → AI не используется, публикуем как есть (ТЗ)."""
    client = _client_with_generate(AssertionError("generate не должен вызываться"))

    title, description = rewrite_video_texts(client, title="", description="  ")

    assert title == ""
    assert description == "  "
    client.generate.assert_not_called()


def test_llm_failure_falls_back_to_original():
    client = _client_with_generate(RuntimeError("LLM недоступна"))

    title, description = rewrite_video_texts(client, title="Название", description="")

    assert title == "Название"
    assert description == ""


def test_empty_llm_answer_falls_back_to_original():
    client = _client_with_generate(["   "])

    title, _ = rewrite_video_texts(client, title="Название", description="")

    assert title == "Название"
