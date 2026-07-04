from app.core.publishing.text_formatting import (
    markdown_to_telegram_html,
    split_hashtags,
    strip_markdown,
)


def test_split_hashtags_extracts_trailing_hashtag_line():
    text = "Первое предложение. Второе предложение.\n\n#технологии #apple #санкции"
    body, hashtags = split_hashtags(text)
    assert body == "Первое предложение. Второе предложение."
    assert hashtags == "#технологии #apple #санкции"


def test_split_hashtags_returns_empty_when_no_hashtag_line():
    text = "Просто текст без хэштегов."
    body, hashtags = split_hashtags(text)
    assert body == "Просто текст без хэштегов."
    assert hashtags == ""


def test_split_hashtags_does_not_treat_inline_hash_as_hashtag_line():
    text = "Цена выросла на #10 позиций в рейтинге."
    body, hashtags = split_hashtags(text)
    assert body == text
    assert hashtags == ""


def test_markdown_to_telegram_html_converts_bold_and_italic():
    text = "**Путин** заявил, что *ситуация стабильна*."
    result = markdown_to_telegram_html(text)
    assert result == "<b>Путин</b> заявил, что <i>ситуация стабильна</i>."


def test_markdown_to_telegram_html_escapes_special_chars():
    text = "Компания <Ромашка> и Ко & партнёры"
    result = markdown_to_telegram_html(text)
    assert "&lt;Ромашка&gt;" in result
    assert "&amp;" in result


def test_strip_markdown_removes_asterisks_without_html_tags():
    text = "**Путин** заявил, что *ситуация стабильна*."
    result = strip_markdown(text)
    assert result == "Путин заявил, что ситуация стабильна."
    assert "<" not in result
