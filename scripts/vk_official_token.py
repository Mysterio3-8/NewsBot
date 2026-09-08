"""Получение ЛИЧНОГО VK-токена через официальное приложение VK ID (OAuth 2.1 + PKCE).

Зачем. Текущие user-токены выпущены сторонними приложениями (Kate Mobile и подобные):
VK считает такой токен признаком автоматизации, и два аккаунта уже улетели в бан
(2026-07-02, 2026-08-03). Токен своего приложения — это ровно тот же доступ, но
выданный официально и отзываемый владельцем.

Почему скрипт, а не «пришли токен в чат». Токен здесь не показывается на экране и не
проходит через переписку: браузер отдаёт одноразовый код на localhost, скрипт меняет
его на токен и сразу пишет в `.env`. В чате токен = засвеченный токен.

Как пользоваться:

    python scripts/vk_official_token.py --client-id 12345678 --env .env --var VK_UPLOAD_TOKEN_3

Перед первым запуском в кабинете приложения (id.vk.ru/about/business/go) нужно:
  * платформа Web, базовый домен `localhost`, доверенный redirect URL `http://localhost`
    (порт не указывается — VK ID поддерживает только 80 и 443);
  * в разделе «Доступы» — права на стену, фото и видео. ⚠️ Это РАСШИРЕННЫЕ доступы: они
    включаются после подтверждения профиля бизнеса, а wall/photos/video выдаются по
    заявке в devsupport@corp.vk.com. Без них токен получится, но публиковать им нельзя.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path

AUTHORIZE_URL = "https://id.vk.com/authorize"
TOKEN_URL = "https://id.vk.com/oauth2/auth"
REDIRECT_URI = "http://localhost"
"""Без порта — VK ID принимает только 80 и 443, и адрес обязан совпасть с кабинетом."""

def _tls_context() -> ssl.SSLContext:
    """Контекст проверки сертификатов.

    Голая установка Python на Windows приходит без корневых сертификатов, и обмен кода
    на токен падал `CERTIFICATE_VERIFY_FAILED` уже ПОСЛЕ успешного входа — то есть
    человек логинился зря, одноразовый код сгорал. Берём набор `certifi`, если он есть;
    нет — системный, как раньше. Проверку не отключаем ни при каких условиях: через это
    соединение идёт токен доступа к аккаунту."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


DEFAULT_SCOPE = "wall photos video offline"
"""Права, которые реально нужны софтам: публикация на стене, загрузка фото и видео.

`offline` — чтобы токен не протухал через сутки; без него придётся перевыпускать
каждый день, а это ровно та ручная работа, от которой уходим."""


class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """Одноразовый приёмник редиректа: забирает `code` из адреса и отпускает поток."""

    code: str | None = None
    state: str | None = None
    device_id: str | None = None

    def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        type(self).code = (query.get("code") or [None])[0]
        type(self).state = (query.get("state") or [None])[0]
        type(self).device_id = (query.get("device_id") or [None])[0]
        body = (
            "Готово, окно можно закрыть."
            if type(self).code
            else "Кода авторизации в ответе нет — смотрите вывод скрипта."
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Молчим: строка лога содержит код авторизации."""


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _wait_for_code(port: int, timeout: int) -> tuple[str, str, str]:
    """Держим приёмник, пока не придёт запрос С КОДОМ или не выйдет время.

    Обслуживать РОВНО один запрос нельзя: браузер стучится на localhost и сам по себе —
    favicon, предзагрузка адресной строки, — и первый такой запрос закрывал сервер за
    доли секунды, ещё до того как человек успевал нажать «Продолжить как»."""
    server = http.server.HTTPServer(("127.0.0.1", port), _CodeCatcher)
    server.timeout = 1

    def serve() -> None:
        deadline = time.monotonic() + timeout
        while _CodeCatcher.code is None and time.monotonic() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    thread.join(timeout + 2)
    server.server_close()
    if not _CodeCatcher.code:
        raise SystemExit(
            "Код авторизации не пришёл. Проверьте, что в кабинете приложения указан "
            f"базовый домен localhost и доверенный redirect URL {REDIRECT_URI}."
        )
    return _CodeCatcher.code, _CodeCatcher.state or "", _CodeCatcher.device_id or ""


def _exchange(client_id: str, code: str, verifier: str, device_id: str, secret: str) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": client_id,
        "device_id": device_id,
        "redirect_uri": REDIRECT_URI,
    }
    if secret:
        # Конфиденциальное приложение: VK ID дополнительно сверяет ключ и IP бэкенда.
        payload["client_secret"] = secret
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=30, context=_tls_context()) as response:
        return json.loads(response.read().decode())


REMOTE_EXCHANGE = r"""
import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path

task = json.loads(base64.b64decode(DATA).decode())
payload = {k: v for k, v in task.items() if k not in ("env_path", "var")}
request = urllib.request.Request(
    "https://id.vk.com/oauth2/auth",
    data=urllib.parse.urlencode(payload).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    data = json.loads(response.read().decode())

if "access_token" not in data:
    print("ОШИБКА VK ID:", json.dumps(data, ensure_ascii=False))
    raise SystemExit(1)

env_path = Path(task["env_path"])
values = {
    task["var"]: data["access_token"],
    task["var"] + "_REFRESH": data.get("refresh_token", ""),
    task["var"] + "_DEVICE_ID": task.get("device_id", ""),
    task["var"] + "_CLIENT_ID": task.get("client_id", ""),
}
lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
written = set()
for index, line in enumerate(lines):
    name = line.split("=", 1)[0].strip()
    if name in values and values[name]:
        lines[index] = name + "=" + values[name]
        written.add(name)
for name, value in values.items():
    if value and name not in written:
        lines.append(name + "=" + value)
env_path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")

print("Токен записан на сервере в", env_path, "как", task["var"])
print("Срок жизни:", data.get("expires_in"), "с; выданные права:", data.get("scope"))
"""
"""Обмен кода на токен, выполняемый НА СЕРВЕРЕ.

🔴 VK ID привязывает токен к IP, с которого его обменяли. Полученный на домашней машине
токен на сервере отвечает `[5] access_token was given to another ip address` и не
работает вовсе — проверено живьём 2026-09-08. Поэтому код ловим в браузере владельца
(редирект идёт на его localhost), а обмен уходит по ssh на прод: там же, где токеном
потом пользуются, и туда же он и записывается.

Токен при этом вообще не появляется на машине владельца и не проходит через переписку.
Скрипт и данные едут серверу через stdin, а не аргументами командной строки: аргументы
видны в `ps` любому процессу на машине, а здесь через них шли бы код авторизации и
секрет PKCE."""


def _ssh_binary() -> str:
    """Виндовый OpenSSH: msys-ssh из Git Bash не находит ключ, если имя пользователя
    Windows написано кириллицей (разобрано 2026-08-14)."""
    windows_ssh = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
    return str(windows_ssh) if windows_ssh.exists() else "ssh"


def _exchange_on_host(host: str, python_bin: str, task: dict) -> None:
    """Меняет код на токен на сервере и там же пишет его в `.env`."""
    # `python -` читает со stdin ПРОГРАММУ, а не данные: JSON, отправленный туда, он
    # молча съедал как выражение и выходил с нулём, ничего не сделав. Поэтому данные
    # вшиваем в текст программы строкой base64, а на stdin отдаём саму программу —
    # в аргументы командной строки они не попадают и в `ps` не видны.
    payload = base64.b64encode(json.dumps(task).encode()).decode()
    program = 'DATA = "' + payload + '"\n' + REMOTE_EXCHANGE
    completed = subprocess.run(
        [_ssh_binary(), "-o", "BatchMode=yes", host, f"{python_bin} - "],
        input=program.encode(),
        capture_output=True,
    )
    output = completed.stdout.decode(errors="replace").strip()
    errors = completed.stderr.decode(errors="replace").strip()
    if output:
        print(output)
    if completed.returncode != 0:
        raise SystemExit(f"Обмен на сервере не удался: {errors or output}")


def _write_env(env_path: Path, variable: str, token: str) -> None:
    """Дописывает или заменяет переменную, не трогая остальные строки файла.

    Именно дописывает: `.env` на проде содержит секреты других софтов, и перезапись
    файла целиком однажды снесла бы их вместе с доступом."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{variable}="):
            lines[index] = f"{variable}={token}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{variable}={token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True, help="ID приложения из кабинета VK ID")
    parser.add_argument(
        "--client-secret",
        default="",
        help="Защищённый ключ. Нужен только конфиденциальному приложению",
    )
    parser.add_argument("--env", default=".env", help="Куда записать токен")
    parser.add_argument("--var", default="VK_UPLOAD_TOKEN_OFFICIAL", help="Имя переменной")
    parser.add_argument("--scope", default=DEFAULT_SCOPE, help="Запрашиваемые права")
    parser.add_argument("--port", type=int, default=80, help="Порт localhost (80 или 443)")
    parser.add_argument("--timeout", type=int, default=300, help="Сколько ждать вход, секунд")
    parser.add_argument(
        "--exchange-host",
        default="",
        help="Меняем код на токен НА ЭТОМ сервере по ssh и туда же пишем .env. "
        "Нужно потому, что VK ID привязывает токен к IP обмена",
    )
    parser.add_argument(
        "--remote-env",
        default="/opt/news-rewriter/.env",
        help="Путь к .env на сервере (только с --exchange-host)",
    )
    parser.add_argument(
        "--remote-python",
        default="/opt/news-rewriter/venv/bin/python",
        help="Каким питоном выполнять обмен на сервере",
    )
    args = parser.parse_args()

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": args.client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": args.scope,
        "device_id": str(uuid.uuid4()),
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    print("Открываю окно входа VK ID. Войдите тем аккаунтом, от имени которого публикуем.")
    print(f"Если браузер не открылся сам — адрес:\n{url}\n")
    webbrowser.open(url)

    code, returned_state, device_id = _wait_for_code(args.port, args.timeout)
    if returned_state != state:
        # Чужой ответ на наш редирект: меняем его на токен только при совпадении state.
        raise SystemExit("state не совпал — ответ пришёл не на наш запрос, токен не беру")

    if args.exchange_host:
        # Токен останется на сервере: сюда он не приезжает вовсе.
        _exchange_on_host(
            args.exchange_host,
            args.remote_python,
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": args.client_id,
                "device_id": device_id,
                "redirect_uri": REDIRECT_URI,
                "env_path": args.remote_env,
                "var": args.var,
            },
        )
        return

    data = _exchange(args.client_id, code, verifier, device_id, args.client_secret)
    token = data.get("access_token")
    if not token:
        raise SystemExit(f"VK ID не отдал токен: {data}")

    env_path = Path(args.env)
    _write_env(env_path, args.var, token)
    # Печатаем что угодно, кроме самого токена: вывод уходит в терминал и в историю.
    print(f"Токен записан в {env_path} как {args.var}.")
    print(f"Срок жизни: {data.get('expires_in', 'не указан')} с; выданные права: {data.get('scope', '?')}")
    if "refresh_token" in data:
        _write_env(env_path, f"{args.var}_REFRESH", data["refresh_token"])
        print(f"Refresh-токен записан как {args.var}_REFRESH.")
    # device_id обязателен при обновлении токена, и это НЕ тот идентификатор, который мы
    # сгенерировали в запросе: VK ID возвращает свой. Без него обновить час жизни нечем,
    # и «бессрочный» доступ пришлось бы каждый раз получать входом руками.
    if device_id:
        _write_env(env_path, f"{args.var}_DEVICE_ID", device_id)
        print(f"Идентификатор устройства записан как {args.var}_DEVICE_ID.")
    _write_env(env_path, f"{args.var}_CLIENT_ID", args.client_id)


if __name__ == "__main__":
    main()
