from app.core.llm.sanitizer import strip_foreign_script_artifacts


def test_strip_foreign_script_artifacts_removes_katakana():
    """Регрессия: llama-3.1-8b-instant иногда вставляет катакану в редкие имена
    (наблюдалось "Мютцинец" -> "Мュцениц")."""
    text = "Агата Мュцениц подверглась скандалу"
    assert strip_foreign_script_artifacts(text) == "Агата Мцениц подверглась скандалу"


def test_strip_foreign_script_artifacts_removes_cjk_ideographs():
    text = "Новость про 中国 экономику"
    assert strip_foreign_script_artifacts(text) == "Новость про  экономику"


def test_strip_foreign_script_artifacts_removes_hangul():
    text = "Слово 한글 тест"
    assert strip_foreign_script_artifacts(text) == "Слово  тест"


def test_strip_foreign_script_artifacts_leaves_normal_russian_text_untouched():
    text = "Обычный русский текст без артефактов."
    assert strip_foreign_script_artifacts(text) == text
