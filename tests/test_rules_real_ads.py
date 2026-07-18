"""Регрессионные тесты на реальных примерах нативной/партнёрской рекламы из VK,
присланных пользователем 2026-07-01 (find_blacklisted_word, стоп-слова новостей)
и 2026-07-18 (find_ad_marker — общий фильтр рекламы ВСЕХ каналов, включая «лить всё»).
"""
from app.config.loader import load_config
from app.core.filtering.rules import find_ad_marker, find_blacklisted_word

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


# Реальные примеры рекламы от пользователя 2026-07-18 — должен ловить find_ad_marker
# (общий фильтр всех каналов, работает и при filters_enabled=False у Кино).
REAL_AD_EXAMPLES_2026_07_18 = [
    (
        "yandex_topup_bonus",
        "vk.cc/ccjE1C Пополните счет на 10 000 ₽ и Яндекс добавит вам еще 5 000 ₽. "
        "Подробнее на сайте!",
    ),
    (
        "carpet_shop",
        "🍿🎬Смотри любимые фильмы с уютным ковром от Аграба.🎥 vk.cc/cZCGHD "
        "Более 500 дизайнов под любой интерьер.",
    ),
    (
        "drain_gel_ozon_wb",
        "Гель растворит волосы, остатки пищи и другие загрязнения. При этом трубы и "
        "септики целы!\n\nСсылка на OZON: vk.cc/cZuxrl\nСсылка на WB: vk.cc/cZuxrk\n"
        "Заказывайте сразу, пока не раскупили по выгодной цене!\n\n"
        "У геля для труб WONDER LAB оценка 4,8 на основе более 70 000 отзывов",
    ),
    (
        "region_hoodies",
        "Те самые именные толстовки с контуром твоего региона - новый тренд этого года💯\n"
        "Посмотреть наличие: vk.cc/cYvfTs\n\n✅Индивидуальный дизайн\n"
        "✅Большой размерный ряд, от XS до 6XL\n✅Премиальное качество материалов\n"
        "✅Собственное производство\n✅Быстрая доставка по всей территории РФ\n\n"
        'Жми "Узнать цены", распродаем остатки по себестоимости💪\nvk.cc/cYvfTs',
    ),
    (
        "photo_statuette_dm_funnel",
        "Она привыкла получать цветы и украшения. Но такого ей ещё точно не дарили… ❤‍🔥\n"
        "Персональная статуэтка по фото — как будто её сделали для выставки.\n"
        "💬 Напиши «Привет» в личные сообщения — чтобы посмотреть, как будет выглядеть "
        "её статуэтка.\n\nПодробнее: vk.cc/cZuT5j",
    ),
]


def test_real_ads_2026_07_18_caught_by_ad_marker():
    for name, text in REAL_AD_EXAMPLES_2026_07_18:
        assert find_ad_marker(text) is not None, f"Реклама '{name}' не отловлена"


def test_real_ads_2026_07_18_caught_even_without_shortener_link():
    """Ссылку vk.cc легко заменить — реклама должна ловиться и по продающим оборотам.
    Исключение — carpet_shop: без ссылки в нём нет ни одного продающего оборота
    («смотри фильмы с ковром, 500 дизайнов»), такой пост ловится только по ссылке."""
    for name, text in REAL_AD_EXAMPLES_2026_07_18:
        if name == "carpet_shop":
            continue
        stripped = (
            text.replace("vk.cc/", "example.com/")
            .replace("Подробнее на сайте", "Детали на сайте")
        )
        assert find_ad_marker(stripped) is not None, f"Реклама '{name}' без vk.cc не отловлена"


def test_kino_and_news_texts_not_falsely_flagged_as_ads():
    genuine_texts = [
        # Кино-пост: анонс фильма
        "«Интерстеллар» возвращается в кинотеатры: команда исследователей отправляется "
        "сквозь червоточину, чтобы спасти человечество. Мэттью Макконахи в главной роли.",
        # Новость с «заказом» (не должна ловиться на подстроку «заказ»)
        "Минобороны разместило оборонный заказ на новые комплексы. Поставки начнутся "
        "в следующем году.",
        # Новость про экономику со словом «счёт»
        "Сборная России открыла счет на пятой минуте матча и удержала преимущество.",
    ]
    for text in genuine_texts:
        assert find_ad_marker(text) is None, f"Ложное срабатывание: {text[:60]!r}"
