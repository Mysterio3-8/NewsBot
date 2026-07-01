from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from app.core.llm.client import LLMClient
from app.ui.pages.ai_page import AIPage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_prompt_list_loads_txt_files_from_prompts_dir(qapp):
    page = AIPage()
    names = [page.prompt_list.item(i).text() for i in range(page.prompt_list.count())]
    assert "system" in names
    assert "classifier" in names


def test_selecting_prompt_loads_its_content(qapp):
    page = AIPage()
    page.prompt_list.setCurrentRow(0)
    assert len(page.editor.toPlainText()) > 0


def test_save_writes_edited_content_back_to_file(qapp, tmp_path, monkeypatch):
    import app.ui.pages.ai_page as ai_page_module

    prompt_file = tmp_path / "test_prompt.txt"
    prompt_file.write_text("исходный текст", encoding="utf-8")
    monkeypatch.setattr(ai_page_module, "PROMPTS_DIR", tmp_path)

    page = AIPage()
    page.prompt_list.setCurrentRow(0)
    page.editor.setPlainText("новый текст")
    page._on_save()

    assert prompt_file.read_text(encoding="utf-8") == "новый текст"


def test_check_llm_shows_unavailable_status(qapp):
    page = AIPage()
    client = Mock(spec=LLMClient)
    client.is_running.return_value = False
    page.bind_llm_client(client)

    page._on_check_llm()

    assert "недоступна" in page.llm_status_label.text()


def test_check_llm_shows_ready_status(qapp):
    page = AIPage()
    client = Mock(spec=LLMClient)
    client.is_running.return_value = True
    client.is_model_downloaded.return_value = True
    page.bind_llm_client(client)

    page._on_check_llm()

    assert "готова" in page.llm_status_label.text()
