"""Веб-лаунчер: одна кнопка "Старт"/"Стоп" вместо командной строки/десктоп-UI.
Тонкий Flask-слой поверх общего ServiceController (та же логика управления сервисом,
что и у Telegram-бота — см. app/service_controller.py)."""
from __future__ import annotations

import logging
import threading
import webbrowser

from flask import Flask, jsonify, render_template

from app.moscow_time import format_moscow_time
from app.paths import PROJECT_ROOT
from app.service_controller import ServiceController

logger = logging.getLogger("app")

WEB_PORT = 5000

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "app" / "templates"))
_controller = ServiceController()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    return jsonify(
        running=_controller.is_running(),
        started_at=format_moscow_time(_controller.started_at),
        recent_published=[
            {"headline": post.headline, "published_at": format_moscow_time(post.published_at)}
            for post in _controller.recent_published(limit=10)
        ],
    )


@app.route("/start", methods=["POST"])
def start():
    started = _controller.start()
    return jsonify(ok=started, message="Запущено" if started else "Уже запущено")


@app.route("/stop", methods=["POST"])
def stop():
    stopped = _controller.stop()
    return jsonify(ok=stopped, message="Остановлено" if stopped else "Уже остановлено")


def main() -> None:
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")).start()
    app.run(host="127.0.0.1", port=WEB_PORT)


if __name__ == "__main__":
    main()
