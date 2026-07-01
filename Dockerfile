FROM python:3.12-slim

WORKDIR /app

# Только headless-режим: PySide6 ставится (нужен для импорта app.main),
# но QApplication никогда не создаётся — системные Qt/X11-библиотеки не нужны.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY prompts/ prompts/
COPY assets/ assets/

RUN mkdir -p data logs output/images

CMD ["python", "app/main.py", "--headless"]
