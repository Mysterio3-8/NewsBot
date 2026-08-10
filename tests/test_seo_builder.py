"""SEO-обвязка публикаций: ключи, теги, поисковые описания."""
from app.core.seo.builder import (
    SeoProfile,
    build_post_seo_tail,
    build_search_line,
    build_tags,
    build_video_seo_description,
)
from app.core.seo.keywords import extract_entities, extract_keywords

KINO_TEXT = (
    "Новый фильм с Робертом Дауни выходит в прокат. Роберт Дауни играет "
    "детектива, а режиссёр Гай Ричи снова собрал звёздный состав. Премьера фильма "
    "состоится осенью."
)


def test_extract_keywords_drops_stopwords_and_short_words():
    keywords = extract_keywords("Он и она в городе, а город большой и красивый город")
    assert "город" in keywords
    assert "она" not in keywords
    assert "и" not in keywords


def test_extract_keywords_orders_by_frequency():
    keywords = extract_keywords("премьера премьера премьера режиссёр")
    assert keywords[0] == "премьера"


def test_extract_entities_finds_person_name():
    assert "Робертом Дауни" in extract_entities(KINO_TEXT)


def test_extract_entities_skips_sentence_start():
    # «Премьера» стоит первым словом предложения — заглавная там по правилам
    # орфографии, а не потому что это имя собственное.
    assert extract_entities("Премьера состоится осенью.") == []


def test_extract_entities_keeps_the_name_after_a_sentence_opening_word():
    """Отбрасываем только первое слово предложения, а не всю фразу целиком."""
    assert extract_entities("Дом сгорел. Губернатор Василий Голубев сообщил.") == [
        "Василий Голубев"
    ]


def test_extract_entities_does_not_glue_names_across_lines():
    """Заголовок и тело — разные строки; склеенный тег вроде «#ростов_беспилотники»
    не ищет никто."""
    assert extract_entities("Дроны атаковали Ростов\nБеспилотники были сбиты") == ["Ростов"]


def test_tags_do_not_include_random_verbs():
    """Частотные слова в теги не идут: «#играет» и «#потерявшего» выглядят накруткой."""
    tags = build_tags(KINO_TEXT, _profile(base_tags=[]), limit=20)
    assert all("играет" not in tag and "потерявшего" not in tag for tag in tags)


def test_extract_entities_has_no_duplicates():
    entities = extract_entities("про Гая Ричи, снова Гая Ричи и опять Гая Ричи")
    assert entities.count("Гая Ричи") == 1


def _profile(**overrides) -> SeoProfile:
    base = dict(
        hashtag_group="kinobestfilmss",
        base_tags=["кино", "фильмы"],
        search_phrases=["{q} смотреть онлайн", "{q} трейлер"],
        post_tag_limit=4,
        video_tag_limit=10,
        links=["📲 Telegram: https://t.me/kinobestfilmss"],
    )
    base.update(overrides)
    return SeoProfile(**base)


def test_build_tags_puts_names_first_and_binds_them_to_group():
    """Имена — то, что реально набирают в поиске; постоянных тегов пять, а лимит
    поста шесть — впереди они не оставили бы имени места ни разу."""
    tags = build_tags(KINO_TEXT, _profile(), limit=4)
    assert tags[0] == "#робертом_дауни@kinobestfilmss"
    assert "#кино@kinobestfilmss" in tags
    assert len(tags) == 4


def test_build_tags_falls_back_to_channel_tags_without_names():
    tags = build_tags("просто немного текста без имён", _profile(), limit=3)
    assert tags[0] == "#кино@kinobestfilmss"


def test_build_tags_without_group_has_no_at_suffix():
    tags = build_tags(KINO_TEXT, _profile(hashtag_group=""), limit=2)
    assert tags[0] == "#робертом_дауни"


def test_build_tags_deduplicates_across_sources():
    tags = build_tags("текст про кино кино кино", _profile(base_tags=["кино"]), limit=5)
    assert tags.count("#кино@kinobestfilmss") == 1


def test_build_tags_respects_zero_limit():
    assert build_tags(KINO_TEXT, _profile(), limit=0) == []


def test_post_tail_is_single_line_of_tags():
    tail = build_post_seo_tail(KINO_TEXT, _profile())
    assert "\n" not in tail
    assert tail.startswith("#")


def test_post_tail_empty_when_nothing_to_tag():
    assert build_post_seo_tail("", _profile(base_tags=[])) == ""


def test_search_line_uses_whole_phrases():
    line = build_search_line(KINO_TEXT, _profile())
    assert "Робертом Дауни смотреть онлайн" in line
    assert "Робертом Дауни трейлер" in line


def test_search_line_empty_without_templates():
    assert build_search_line(KINO_TEXT, _profile(search_phrases=[])) == ""


def test_video_description_starts_with_title_not_tags():
    description = build_video_seo_description(
        title="Детектив Стая", body=KINO_TEXT, profile=_profile()
    )
    assert description.startswith("Детектив Стая")
    # Теги — в самом низу: сниппет поисковика берёт первые строки.
    assert description.rstrip().splitlines()[-1].startswith("#")


def test_video_description_contains_links_and_search_phrases():
    description = build_video_seo_description(
        title="Детектив Стая", body=KINO_TEXT, profile=_profile()
    )
    assert "https://t.me/kinobestfilmss" in description
    assert "смотреть онлайн" in description


def test_seo_tail_replaces_hashtags_left_by_the_llm():
    """Публикатор берёт под теги РОВНО последнюю строку. Оставь мы обе, LLM-строка
    зависла бы посреди текста, а наша стала бы единственной «настоящей»."""
    from app.core.pipeline import _append_seo_tail

    result = _append_seo_tail(f"{KINO_TEXT}\n\n#кинчик #смотрим", _profile())

    assert "#кинчик" not in result
    assert result.rstrip().splitlines()[-1].startswith("#")


def test_seo_tail_keeps_text_untouched_when_nothing_to_tag():
    """Ни постоянных тегов канала, ни имён, ни значимых слов — текст не трогаем."""
    from app.core.pipeline import _append_seo_tail

    text = "и в на о за"
    assert _append_seo_tail(text, _profile(base_tags=[])) == text


def test_video_description_is_trimmed_on_word_boundary():
    description = build_video_seo_description(
        title="Заголовок", body="слово " * 500, profile=_profile(), limit=120
    )
    assert len(description) <= 121  # плюс многоточие
    assert description.endswith("…")
