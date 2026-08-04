"""Дым-тест полного пути публикации С МЕДИА: загрузка фото → вложение → пост → удаление.

Зачем отдельный тест, если есть 791 юнит-тест: поломка 2026-08-04 (месяц постов голым
текстом без фото и фильмов) юнит-тестами не ловилась в принципе. Там всё было зелёное —
ломался стык живых частей: пул токенов отдавал None, публикатор штатно уходил в
best-effort и постил текст. Владелец: «после каждого нового обновления надо делать один
тестовый пост». Пост без фото таким тестом быть не может — он бы ту поломку не заметил.

Запуск (на сервере, после деплоя):
    venv/bin/python -m app.smoke_media_publish

Три исхода, различать их важно:
    OK        — фото доехало до стены, пост удалён. Путь публикации жив.
    ОТЛОЖЕНО  — все аккаунты пула заняты зазором. НЕ поломка кода: публикатор
                корректно отказался публиковать пост с медиа голым текстом.
    ПРОВАЛ    — пост опубликовался БЕЗ вложения либо VK вернул ошибку. Это регрессия.

Код возврата: 0 для OK и ОТЛОЖЕНО, 1 для ПРОВАЛА — deploy.sh валит деплой только на
настоящей регрессии, а не на занятом пуле.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger("smoke")

SMOKE_TEXT = "[тех. проверка публикации, удаляется автоматически]"
VK_API_VERSION = "5.199"


def _build_probe_image(directory: Path) -> Path:
    """Синтетическая картинка: тест не должен зависеть от того, что лежит в output/.

    Пробник обязан быть ПОХОЖ НА ОБЫЧНОЕ ФОТО. Проверено вживую 2026-08-04, upload-сервер
    VK отвечает `photo: ""` (а `saveWallPhoto` затем падает `[100] photo is undefined`)
    на двух крайностях: одноцветная заливка и сплошной RGB-шум (несжимаемый, ~580 КБ на
    1200×800). Настоящие фото из `output/` тем же токеном в ту же секунду грузятся
    штатно. Поэтому здесь плавный градиент с несколькими фигурами: обычный размер файла,
    нормальная сжимаемость.

    Уникальность на каждый запуск обязательна: байт-в-байт тот же файл VK отвергает как
    повторную загрузку (так и всплыла первая ошибка — одноцветный пробник загрузился
    один раз, а второй такой же уже нет)."""
    import random

    from PIL import Image, ImageDraw

    width, height = 1200, 800
    rng = random.SystemRandom()
    base = (rng.randint(20, 90), rng.randint(60, 140), rng.randint(60, 140))
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    # Плавный вертикальный градиент — сжимается как обычная фотография.
    for y in range(height):
        shade = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(
                int(base[0] + shade * 90),
                int(base[1] + shade * 70),
                int(base[2] + shade * 50),
            ),
        )
    for _ in range(6):
        x0, y0 = rng.randint(0, width - 200), rng.randint(0, height - 200)
        draw.ellipse(
            [x0, y0, x0 + rng.randint(80, 200), y0 + rng.randint(80, 200)],
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
        )
    path = directory / "smoke_probe.jpg"
    image.save(path, quality=88)
    return path


def _wall_attachments(token: str, group_id: int, post_id: int) -> list[str]:
    response = requests.get(
        "https://api.vk.com/method/wall.getById",
        params={
            "access_token": token,
            "posts": f"-{group_id}_{post_id}",
            "v": VK_API_VERSION,
        },
        timeout=30,
    ).json()
    payload = response.get("response") or {}
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not items:
        return []
    return [attachment["type"] for attachment in (items[0].get("attachments") or [])]


def _delete_post(token: str, group_id: int, post_id: int) -> None:
    """wall.delete групповым токеном недоступен (VK [27]) — удаляем личным."""
    response = requests.get(
        "https://api.vk.com/method/wall.delete",
        params={
            "access_token": token,
            "owner_id": -group_id,
            "post_id": post_id,
            "v": VK_API_VERSION,
        },
        timeout=30,
    ).json()
    if "response" not in response:
        logger.warning(
            "Тестовый пост %s НЕ удалён: %s — убрать вручную",
            post_id,
            response.get("error", {}).get("error_msg"),
        )


def run_smoke(channel_match: str, verify_token_env: str) -> int:
    from app.core.publishing.vk_token_pool import read_env_file
    from app.db.repository import Repository, make_engine
    from app.factories import build_vk_publisher_for_channel

    repo = Repository(make_engine())
    channels = [
        channel
        for channel in repo.list_channels(enabled_only=True)
        if channel_match.lower() in channel.name.lower() and channel.vk_destination
    ]
    if not channels:
        print(f"ПРОВАЛ: не найден включённый канал с VK-приёмником по «{channel_match}»")
        return 1

    channel = channels[0]
    group_id = int(channel.vk_destination)
    publisher = build_vk_publisher_for_channel(channel)
    if publisher is None:
        print(f"ПРОВАЛ: publisher канала «{channel.name}» не собрался (нет группового токена)")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        image = _build_probe_image(Path(tmp))
        result = publisher.publish(group_id=group_id, text=SMOKE_TEXT, image_paths=[image])

    if not result.success:
        # Отличать отложенную публикацию от НАСТОЯЩЕЙ ошибки VK обязательно: без этого
        # тест печатал «ОТЛОЖЕНО» и отдавал 0 на [214] Access to adding post denied,
        # то есть глушил ровно ту поломку, ради которой написан.
        from app.core.publishing.vk_publisher import POSTPONED_PREFIX

        if (result.error or "").startswith(POSTPONED_PREFIX):
            print(f"ОТЛОЖЕНО: {result.error}")
            print("Пул токенов занят. Это не регрессия — медиа-пост не ушёл голым текстом.")
            return 0
        print(f"ПРОВАЛ: VK отказал в публикации — {result.error}")
        return 1

    verify_token = read_env_file("/etc/vk-tokens.env").get(verify_token_env)
    if not verify_token:
        print(f"ОТЛОЖЕНО: пост {result.post_id} создан, но {verify_token_env} нет — удали вручную")
        return 0

    attachments = _wall_attachments(verify_token, group_id, result.post_id)
    _delete_post(verify_token, group_id, result.post_id)

    if not attachments:
        print(f"ПРОВАЛ: пост {result.post_id} опубликован БЕЗ вложения — медиа не доехало")
        return 1

    print(f"OK: пост {result.post_id} опубликован с вложением {attachments}, удалён")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="Кино", help="подстрока имени канала")
    parser.add_argument("--verify-token-env", default="VK_UPLOAD_TOKEN_1")
    parser.add_argument("--env-file", default="/opt/news-rewriter/.env")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv(args.env_file)
    return run_smoke(args.channel, args.verify_token_env)


if __name__ == "__main__":
    sys.exit(main())
