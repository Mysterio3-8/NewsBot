"""Продление официального VK-токена по refresh-токену.

Зачем. VK ID выдаёт access-токен ровно на час — «бессрочных» токенов там больше нет,
`offline` даёт не вечный ключ, а право обновлять его без участия человека. Без этого
модуля чтение источников встаёт через час после каждого входа, а вход — это ручная
работа владельца в браузере.

⚠️ Refresh-токен ОДНОРАЗОВЫЙ: VK возвращает новый вместе с каждым access-токеном, и
старый сразу перестаёт работать. Не сохранить новый = порвать цепочку и снова звать
владельца. Поэтому запись идёт до того, как мы порадуемся успеху, и в два места сразу:
в `.env` (переживёт перезапуск) и в `os.environ` (подхватит живой процесс — фетчер
собирается заново на каждом цикле и читает переменную оттуда).

⚠️ Обновлять надо ТАМ ЖЕ, где токеном пользуются: VK ID привязывает выданный токен к IP
обмена. Обновление с другой машины вернёт токен, который на проде ответит
`[5] access_token was given to another ip address` — проверено живьём 2026-09-08.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("publishing")

TOKEN_URL = "https://id.vk.com/oauth2/auth"

DEFAULT_ENV_PATH = Path("/opt/news-rewriter/.env")

REFRESH_MARGIN_MINUTES = 15
"""Насколько раньше срока обновляемся. Токен живёт час, джоб ходит чаще — запас нужен на
случай, если один прогон не состоялся: сеть моргнула, сервис перезапускался."""


class TokenRefreshError(RuntimeError):
    """Обновить не удалось. Наверх поднимается ровно эта ошибка, без деталей VK в тексте:
    в ответе может оказаться сам токен, а сообщения ошибок уходят в журнал."""


def _request_new_token(client_id: str, refresh_token: str, device_id: str) -> dict:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "device_id": device_id,
    }
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def write_env_values(env_path: Path, values: dict[str, str]) -> None:
    """Обновляет переменные в `.env`, не трогая остальные строки.

    Именно построчно, а не перезаписью файла целиком: в `.env` на проде лежат секреты
    всех софтов, и полная перезапись однажды снесла бы их вместе с доступом."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    written: set[str] = set()
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in values and values[name]:
            lines[index] = f"{name}={values[name]}"
            written.add(name)
    for name, value in values.items():
        if value and name not in written:
            lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def refresh_official_token(
    variable: str = "VK_TOKEN_OFFICIAL",
    *,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> bool:
    """Продлевает один токен. Возвращает True, если токен обновлён.

    Отсутствие любой из трёх переменных — не ошибка, а «этот токен не заводили»: софт
    работает и на старом `VK_USER_TOKEN`, и падать из-за ненастроенного канала нельзя.
    """
    env = os.environ if environ is None else environ
    path = env_path or DEFAULT_ENV_PATH

    refresh_token = env.get(f"{variable}_REFRESH", "")
    device_id = env.get(f"{variable}_DEVICE_ID", "")
    client_id = env.get(f"{variable}_CLIENT_ID", "")
    if not (refresh_token and device_id and client_id):
        logger.debug("VK-токен %s не настроен для продления — пропускаю", variable)
        return False

    try:
        data = _request_new_token(client_id, refresh_token, device_id)
    except Exception as error:  # noqa: BLE001 — граница внешнего API
        logger.warning("VK: продлить %s не удалось: %s", variable, error)
        return False

    access_token = data.get("access_token", "")
    if not access_token:
        # Чаще всего это «refresh-токен уже использован» — цепочка порвана, и вернуть её
        # можно только новым входом владельца. Пишем явно, чтобы это не выглядело сетевым
        # сбоем, который сам пройдёт.
        logger.error(
            "VK: %s не продлён, VK ID отказал (%s). Нужен новый вход владельца",
            variable,
            data.get("error", "без кода"),
        )
        return False

    values = {
        variable: access_token,
        f"{variable}_REFRESH": data.get("refresh_token", refresh_token),
    }
    try:
        write_env_values(path, values)
    except OSError as error:
        # Токен уже выдан, а старый refresh больше не работает: если не записать, следующий
        # прогон обновить не сможет. Поэтому в память кладём в любом случае — процесс
        # доживёт до починки файла.
        logger.error("VK: %s продлён, но записать в %s не вышло: %s", variable, path, error)
        env.update(values)
        return True

    env.update(values)
    logger.info("VK: %s продлён на %s с", variable, data.get("expires_in", "?"))
    return True


def build_token_refresh_job(variables: tuple[str, ...] = ("VK_TOKEN_OFFICIAL",)):
    """Задача планировщика: продлить все официальные токены."""

    def job() -> None:
        for variable in variables:
            refresh_official_token(variable)

    return job
