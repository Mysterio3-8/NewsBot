FROM python:3.12-slim

WORKDIR /app

# ffmpeg — обязателен для watermark на видео и AI-уникализатора (app/core/video,
# app/core/media). Без него эти шаги падают явной ошибкой. Только headless-режим:
# PySide6 ставится (нужен для импорта app.main), но QApplication не создаётся —
# системные Qt/X11-библиотеки не нужны.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY prompts/ prompts/
COPY assets/ assets/

RUN mkdir -p data logs output/images

# Дефолт — 24/7 пайплайн. Для управления через Telegram-бота вместо этого запускать
# `python -m app.control_bot` (см. docker-compose профиль "bot").
CMD ["python", "app/main.py", "--headless"]
