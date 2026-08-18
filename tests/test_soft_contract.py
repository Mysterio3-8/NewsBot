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


# --- жанры кнопки «Сборник по жанру» (2026-08-18) ----------------------------


def test_genres_survive_json_roundtrip():
    from app.manager.contract import SoftContract

    contract = SoftContract().with_genre("Фонк", "русский фонк").with_genre("Рэп", "русский рэп")
    restored = SoftContract.from_config_json(contract.to_config_json())

    assert restored.genres == (("Фонк", "русский фонк"), ("Рэп", "русский рэп"))


def test_genre_without_query_searches_by_its_name():
    from app.manager.contract import SoftContract

    assert SoftContract().with_genre("Шансон").genres == (("Шансон", "Шансон"),)


def test_same_genre_twice_replaces_the_query():
    """Две кнопки «Фонк» с разными запросами владелец не различит.

    Побеждает ПОСЛЕДНЕЕ написание имени: владелец перенабрал жанр осознанно, и
    навязывать ему прежний регистр незачем."""
    from app.manager.contract import SoftContract

    contract = SoftContract().with_genre("Фонк", "фонк").with_genre("фонк", "русский фонк")

    assert contract.genres == (("фонк", "русский фонк"),)


def test_last_genre_can_be_removed_unlike_the_last_source():
    """Пустой список жанров — это «кнопка берёт список из config.yaml софта»,
    а не «софту негде брать контент», поэтому запрета на удаление нет."""
    from app.manager.contract import SoftContract

    assert SoftContract().with_genre("Фонк").without_genre("Фонк").genres == ()


def test_broken_genre_entry_does_not_crash_the_bot():
    from app.manager.contract import SoftContract

    restored = SoftContract.from_config_json('{"genres": ["строка", {"query": "нет имени"}]}')

    assert restored.genres == ()


def test_contract_summary_counts_genres():
    from app.manager.contract import SoftContract

    assert "🎼 Жанров: 2" in (
        SoftContract().with_genre("Фонк").with_genre("Рэп").render_summary()
    )
