from app.core.manual_post import PostButton, parse_button_input, render_preview


def test_parse_button_input_parses_text_and_url():
    button = parse_button_input("Читать далее | https://t.me/NewsThreeWord")
    assert button == PostButton(text="Читать далее", url="https://t.me/NewsThreeWord")


def test_parse_button_input_strips_whitespace():
    button = parse_button_input("  Сайт  |  https://example.com  ")
    assert button == PostButton(text="Сайт", url="https://example.com")


def test_parse_button_input_rejects_missing_separator():
    assert parse_button_input("Просто текст без ссылки") is None


def test_parse_button_input_rejects_non_http_url():
    assert parse_button_input("Кнопка | t.me/channel") is None
    assert parse_button_input("Кнопка | javascript:alert(1)") is None


def test_parse_button_input_rejects_empty_text():
    assert parse_button_input(" | https://example.com") is None


def test_render_preview_without_buttons():
    assert render_preview("Текст поста", []) == "Текст поста"


def test_render_preview_lists_buttons():
    preview = render_preview(
        "Заголовок новости",
        [PostButton(text="Источник", url="https://example.com")],
    )
    assert "Заголовок новости" in preview
    assert "Кнопки под постом:" in preview
    assert "• Источник → https://example.com" in preview


def test_render_preview_handles_empty_text():
    assert render_preview("", [PostButton(text="A", url="https://a.com")]).startswith("(без текста)")
