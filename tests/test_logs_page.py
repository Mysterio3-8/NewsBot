from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.ui.pages.logs_page import LogsPage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_refresh_shows_placeholder_when_file_missing(qapp, tmp_path, monkeypatch):
    import app.ui.pages.logs_page as logs_page_module

    monkeypatch.setattr(logs_page_module, "LOGS_DIR", tmp_path)

    page = LogsPage()
    page.refresh()

    assert "ещё не создан" in page.text_view.toPlainText()


def test_refresh_shows_last_lines_of_log_file(qapp, tmp_path, monkeypatch):
    import app.ui.pages.logs_page as logs_page_module

    monkeypatch.setattr(logs_page_module, "LOGS_DIR", tmp_path)
    log_content = "\n".join(f"строка {i}" for i in range(300))
    (tmp_path / "app.log").write_text(log_content, encoding="utf-8")

    page = LogsPage()
    page.refresh()

    shown_lines = page.text_view.toPlainText().splitlines()
    assert len(shown_lines) == 200
    assert shown_lines[-1] == "строка 299"


def test_open_logs_folder_calls_startfile(qapp, tmp_path, monkeypatch):
    import app.ui.pages.logs_page as logs_page_module

    monkeypatch.setattr(logs_page_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(logs_page_module.sys, "platform", "win32")

    page = LogsPage()
    with patch("app.ui.pages.logs_page.os.startfile", create=True) as mock_startfile:
        page._open_logs_folder()

    mock_startfile.assert_called_once_with(tmp_path)
