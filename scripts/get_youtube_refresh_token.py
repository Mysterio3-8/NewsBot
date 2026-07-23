"""Одноразовый помощник: получить refresh_token для загрузки на свой YouTube-канал.

Запускается ЛОКАЛЬНО (нужен браузер), один раз. Печатает три значения для .env:
YT_UPLOAD_CLIENT_ID / YT_UPLOAD_CLIENT_SECRET / YT_UPLOAD_REFRESH_TOKEN.

Что нужно заранее (всё бесплатно):
1. console.cloud.google.com → создать проект → APIs & Services → включить "YouTube Data API v3".
2. OAuth consent screen: тип External, добавить свой Google-аккаунт в Test users.
3. Credentials → Create Credentials → OAuth client ID → тип "Desktop app".
   Скачать JSON, положить рядом как client_secret.json (или указать путь аргументом).

Запуск:
    python scripts/get_youtube_refresh_token.py [путь_к_client_secret.json]
"""
from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    client_secret = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    # access_type=offline + prompt=consent — обязательны, иначе Google не вернёт
    # refresh_token (без него сервер не сможет обновлять доступ без браузера).
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent"
    )

    print("\n=== Скопируй в .env на сервере ===")
    print(f"YT_UPLOAD_CLIENT_ID={creds.client_id}")
    print(f"YT_UPLOAD_CLIENT_SECRET={creds.client_secret}")
    print(f"YT_UPLOAD_REFRESH_TOKEN={creds.refresh_token}")
    print("YT_UPLOAD_PRIVACY=public")


if __name__ == "__main__":
    main()
