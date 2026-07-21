"""AI-хуки для клипов: разбор ответа, добивка до нужного количества, fail-open."""
from __future__ import annotations

from unittest.mock import patch

from app.core.llm.client import LLMClient
from app.core.llm.clip_hook import generate_clip_hooks, parse_hooks


def _client() -> LLMClient:
    return LLMClient.__new__(LLMClient)


def test_parse_hooks_reads_json():
    assert parse_hooks('{"hooks": ["Он выжил", "Последний шанс"]}') == ["Он выжил", "Последний шанс"]


def test_parse_hooks_strips_code_fence():
    raw = '```json\n{"hooks": ["Он выжил"]}\n```'

    assert parse_hooks(raw) == ["Он выжил"]


def test_parse_hooks_falls_back_to_lines_and_drops_too_long():
    raw = "- Первый хук\n" + "с" * 60

    assert parse_hooks(raw) == ["Первый хук"]


def test_generate_clip_hooks_renders_real_prompt_with_all_placeholders():
    """render НЕ мокаем — иначе рассинхрон плейсхолдеров промпта и кода не будет пойман."""
    client = _client()
    with patch.object(LLMClient, "load_prompt", side_effect=lambda name: _load(name)), patch.object(
        LLMClient, "generate", return_value='{"hooks": ["Один", "Два", "Три"]}'
    ) as generate:
        hooks = generate_clip_hooks(client, title="Астрал", description="Хоррор", count=3)

    assert hooks == ["Один", "Два", "Три"]
    user_prompt = generate.call_args[0][1]
    assert "{{" not in user_prompt
    assert "Астрал" in user_prompt


def test_generate_clip_hooks_pads_missing_with_film_title():
    client = _client()
    with patch.object(LLMClient, "load_prompt", side_effect=lambda name: _load(name)), patch.object(
        LLMClient, "generate", return_value='{"hooks": ["Один"]}'
    ):
        hooks = generate_clip_hooks(client, title="Астрал", description="", count=3)

    assert hooks == ["Один", "Астрал", "Астрал"]


def test_generate_clip_hooks_falls_back_to_title_when_llm_fails():
    client = _client()
    with patch.object(LLMClient, "load_prompt", side_effect=lambda name: _load(name)), patch.object(
        LLMClient, "generate", side_effect=RuntimeError("LLM недоступна")
    ):
        hooks = generate_clip_hooks(client, title="Астрал", description="", count=2)

    assert hooks == ["Астрал", "Астрал"]


def _load(name: str) -> str:
    from app.paths import PROJECT_ROOT

    return (PROJECT_ROOT / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
