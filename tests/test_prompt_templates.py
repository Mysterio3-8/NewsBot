"""Редактирование текстовых шаблонов (промптов) из бота.

Правка хранится в БД, а не в файле: deploy.sh синкает prompts/ с диска, поэтому
файловая правка затиралась бы при следующем коммите.
"""
from __future__ import annotations

import app.control_bot as bot
from app.core.llm import prompt_store
from app.core.llm.client import LLMClient
from app.config.loader import LLMConfig
from app.db.repository import Repository, init_db, make_engine

TEST_LLM = LLMConfig(
    provider="ollama",
    host="http://localhost:11434",
    model="qwen2.5:7b",
    temperature=0.7,
    top_p=0.9,
    timeout_seconds=5,
    retries=1,
)


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_no_override_by_default(tmp_path):
    repo = make_repo(tmp_path)
    assert prompt_store.get_override(repo, "rewrite") is None
    assert prompt_store.is_overridden(repo, "rewrite") is False


def test_set_and_read_override(tmp_path):
    repo = make_repo(tmp_path)
    prompt_store.set_override(repo, "rewrite", "мой текст")
    assert prompt_store.get_override(repo, "rewrite") == "мой текст"
    assert prompt_store.is_overridden(repo, "rewrite") is True


def test_reset_returns_to_factory(tmp_path):
    repo = make_repo(tmp_path)
    prompt_store.set_override(repo, "rewrite", "мой текст")
    prompt_store.reset_override(repo, "rewrite")
    assert prompt_store.get_override(repo, "rewrite") is None


def test_client_prefers_override_over_file(tmp_path):
    repo = make_repo(tmp_path)
    client = LLMClient(TEST_LLM, repo=repo)
    factory = client.load_prompt("rewrite")
    prompt_store.set_override(repo, "rewrite", "ПЕРЕОПРЕДЕЛЁННЫЙ")
    assert client.load_prompt("rewrite") == "ПЕРЕОПРЕДЕЛЁННЫЙ"
    prompt_store.reset_override(repo, "rewrite")
    assert client.load_prompt("rewrite") == factory


def test_client_without_repo_uses_file():
    """Старый код без repo продолжает работать на заводских промптах."""
    client = LLMClient(TEST_LLM)
    assert client.load_prompt("rewrite").strip() != ""


def test_save_prompt_rejects_empty(tmp_path):
    repo = make_repo(tmp_path)
    assert "Пустой текст" in bot.save_prompt(repo, "rewrite", "   ")
    assert prompt_store.is_overridden(repo, "rewrite") is False


def test_save_prompt_rejects_unknown_name(tmp_path):
    repo = make_repo(tmp_path)
    assert bot.save_prompt(repo, "нет_такого", "текст") == "Неизвестный шаблон."


def test_save_prompt_warns_about_lost_placeholders(tmp_path):
    repo = make_repo(tmp_path)
    result = bot.save_prompt(repo, "rewrite", "текст без плейсхолдеров")
    assert "⚠️" in result  # заводской rewrite содержит {{...}}


def test_save_prompt_no_warning_when_placeholders_kept(tmp_path):
    repo = make_repo(tmp_path)
    client = LLMClient(TEST_LLM)
    factory = client.load_prompt("rewrite")
    result = bot.save_prompt(repo, "rewrite", factory + "\n\nдопиши мысль")
    assert "⚠️" not in result


def test_reset_prompt_reports(tmp_path):
    repo = make_repo(tmp_path)
    prompt_store.set_override(repo, "rewrite", "текст")
    assert "заводскому" in bot.reset_prompt(repo, "rewrite")


def test_prompt_rows_marks_overridden(tmp_path):
    repo = make_repo(tmp_path)
    prompt_store.set_override(repo, "rewrite", "мой")
    rows = {r.name: r for r in bot.prompt_rows(repo)}
    assert rows["rewrite"].overridden is True
    assert rows["system"].overridden is False


def test_render_prompt_card_shows_state(tmp_path):
    repo = make_repo(tmp_path)
    card = bot.render_prompt_card(repo, "rewrite", None)
    assert "заводской" in card
    prompt_store.set_override(repo, "rewrite", "мой уникальный текст")
    card2 = bot.render_prompt_card(repo, "rewrite", None)
    assert "изменён вами" in card2 and "мой уникальный текст" in card2


def test_render_prompt_card_truncates_long_text(tmp_path):
    repo = make_repo(tmp_path)
    prompt_store.set_override(repo, "rewrite", "я" * (bot.PROMPT_PREVIEW_LIMIT + 500))
    assert "показано" in bot.render_prompt_card(repo, "rewrite", None)
