"""Точка входа. `python app/main.py` — десктоп UI, `--headless` — без UI (раздел 19 SPEC.md)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if __name__ == "__main__":
    # `python app/main.py` кладёт в sys.path каталог app/, а не корень проекта —
    # без этого `from app...` абсолютные импорты не резолвятся.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.loader import CONFIG_PATH, load_config
from app.db.repository import Repository, init_db, make_engine
from app.logging_setup import setup_logging

logger = logging.getLogger("app")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI News Rewriter")
    parser.add_argument(
        "--headless", action="store_true", help="Запуск без UI (сервер)"
    )
    return parser.parse_args()


def bootstrap():
    load_dotenv()
    config = load_config()
    setup_logging(config.logging)
    engine = make_engine()
    init_db(engine)
    logger.info("Конфигурация загружена, БД инициализирована")
    return config, engine


def run_headless() -> None:
    from app.core.llm.client import LLMClient
    from app.headless_service import run_forever

    config, engine = bootstrap()
    repo = Repository(engine)
    llm_client = LLMClient(config.llm)

    logger.info("Headless-режим запущен")
    try:
        asyncio.run(run_forever(repo, config, llm_client))
    except KeyboardInterrupt:
        logger.info("Headless-режим остановлен пользователем")


def run_ui() -> None:
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    config, engine = bootstrap()
    repo = Repository(engine)

    app = QApplication(sys.argv)
    window = MainWindow(config=config, config_path=CONFIG_PATH, repo=repo)
    window.show()
    sys.exit(app.exec())


def main() -> None:
    args = parse_args()
    if args.headless:
        run_headless()
    else:
        run_ui()


if __name__ == "__main__":
    main()
