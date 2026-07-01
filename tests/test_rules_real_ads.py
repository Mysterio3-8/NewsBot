"""Регрессионные тесты на реальных примерах нативной/партнёрской рекламы из VK,
присланных пользователем 2026-07-01, чтобы find_blacklisted_word их ловил.
"""
from app.config.loader import load_config
from app.core.filtering.rules import find_blacklisted_word

STOP_WORDS = load_config().filters.stop_words

REAL_AD_EXAMPLES = [
    (
        "mlm_video_income",
        "Ирина работала воспитателем в детском саду. ... заработок на коротких видео. "
        "Технология звучала просто: берёшь популярный формат короткого ролика, "
        "адаптируешь под тематику, добавляешь партнёрские ссылки ... "
        "vk.cc/cZcU0j",
    ),
    (
        "live_stream_ad_label",
        "Прямой эфир!\nПрямой эфир!\nРеклама от автора",
    ),
    (
        "wildberries_hair_removal",
        "Собираюсь с мужем в отпуск, достаю купальник... "
        "Нашла на Wildberries, сейчас ещё и скидка! Артикул 👉 vk.cc/cXSyuo",
    ),
    (
        "nutrition_coach_funnel",
        "Слова этой женщины в автобусе я не забуду никогда. ... "
        "просто перейдите по ссылке и нажмите на кнопку «Получить»: vk.cc/cZd6Hr",
    ),
    (
        "quiz_clickbait",
        "Психологический тест! Узнайте свои сильные и слабые стороны. "
        "После клика обязательно нажмите \"Разрешить\", чтобы начать тест: vk.cc/cYLCIt",
    ),
    (
        "tape_lymphatic_funnel",
        "Мы с Олей дружим со школы... Бесплатный доступ к уроку по ссылке ниже: "
        "vk.com/app5898182_-19028264",
    ),
    (
        "psychologist_quiz",
        "Легендарный тест от психолога Пипа Уилсона. "
        "После перехода нажмите \"Разрешить\", чтобы начать тест: vk.cc/cZb8WN",
    ),
    (
        "wildberries_direct_product",
        "Находка на WB! Набор для ухода за волосами со скидкой 65% - "
        "wildberries.ru/catalog/0/detail.aspx",
    ),
    (
        "papilloma_cream_testimonial",
        "Я боялась, что это навсегда. На шее и под грудью вылезли папилломы... "
        "Артикул: vk.cc/cUT9nD",
    ),
    (
        "fungus_cream_testimonial",
        "Девочки, я смотрела на ноги мамы и плакала. Ей 70, грибок на всех ногтях... "
        "Артикул: vk.cc/cUT9nD",
    ),
]


def test_stop_words_config_contains_expected_markers():
    assert "vk.cc/" in STOP_WORDS
    assert "wildberries.ru/catalog" in STOP_WORDS
    assert "артикул" in STOP_WORDS


def test_real_ad_examples_are_all_caught_by_blacklist():
    for name, text in REAL_AD_EXAMPLES:
        matched = find_blacklisted_word(text, STOP_WORDS)
        assert matched is not None, f"Пример '{name}' не был отловлен стоп-словами: {text[:80]!r}"


def test_genuine_news_is_not_falsely_flagged():
    genuine_news = (
        "Госдума приняла закон о повышении пенсий на 7,5% с 1 января. "
        "Документ был одобрен в третьем чтении большинством голосов."
    )
    assert find_blacklisted_word(genuine_news, STOP_WORDS) is None
