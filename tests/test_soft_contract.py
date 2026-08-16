"""Секция `texts` контракта менеджера — правка текстов внешних софтов из бота.

Спека: `all_auto/specs/bot-external-softs.md`, этап «Тексты».
"""
from app.manager.contract import SoftContract, missing_placeholders


def test_texts_survive_json_roundtrip():
    contract = SoftContract().with_text("template", "🎧 {artist} — {title}")
    contract = contract.with_text("tags", "музыка, новинки")

    restored = SoftContract.from_config_json(contract.to_config_json())

    assert restored.text_post_template == "🎧 {artist} — {title}"
    assert restored.text_base_tags == ("музыка", "новинки")


def test_empty_text_clears_the_setting():
    """Способ вернуться к заводскому значению обязателен: иначе заданный однажды шаблон
    было бы не убрать из бота вовсе."""
    contract = SoftContract().with_text("template", "свой шаблон").with_text("template", "")

    assert contract.text_post_template is None
    assert "texts" not in contract.to_config_dict()


def test_tags_accept_both_commas_and_newlines():
    """Владелец шлёт список одним сообщением — переносы там неизбежны."""
    contract = SoftContract().with_text("phrases", "музыка без цензуры\nновинки, хиты")

    assert contract.text_channel_phrases == ("музыка без цензуры", "новинки", "хиты")


def test_lost_placeholder_is_reported():
    """Потеря плейсхолдера — тихая поломка: пост выходит с пустым местом вместо
    названия, публикация при этом не падает."""
    lost = missing_placeholders("{artist} — {title}", "просто текст {artist}")

    assert lost == ["{title}"]


def test_no_warning_when_placeholders_are_kept():
    assert missing_placeholders("{artist} — {title}", "{title} от {artist}") == []


def test_first_template_has_nothing_to_lose():
    assert missing_placeholders(None, "любой текст") == []
