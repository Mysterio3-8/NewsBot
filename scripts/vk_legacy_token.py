"""Получение личного VK-токена обычным OAuth (implicit) — тем способом, каким они
брались до перехода на VK ID.

Зачем он рядом с `vk_official_token.py`. У VK ID права `wall`/`photos`/`video` —
расширенные: выдаются по заявке в devsupport, а до этого токен умеет только читать.
Обычный OAuth отдаёт их сразу. Пока заявка не отработана, публиковать медиа нечем —
08.09 из-за этого встали все четыре софта.

⚠️ Такой токен ЛОВИТ БАН легче: два аккаунта уже потеряны (2026-07-02, 2026-08-03).
Он временная мера до расширенных прав, а не замена официальному. Антибан-механика
(зазор 60 минут, случайные интервалы, ночные окна) обязательна именно с ним.

Токен в переписку не попадает: браузер отдаёт его на `http://localhost`, скрипт
отправляет на сервер по ssh и пишет прямо в `.env` прода.

⚠️ Токен приходит во ФРАГМЕНТЕ адреса (`#access_token=...`), а фрагмент браузер на
сервер не отправляет вовсе. Поэтому страница отдаёт кусочек JS, который забирает
фрагмент и присылает его отдельным запросом — без этого приёмник видел бы пустой адрес.

Как пользоваться:

    python scripts/vk_legacy_token.py --var VK_PHOTO_UPLOAD_TOKEN \
        --exchange-host news-rewriter-vps --remote-env /opt/news-rewriter/.env
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

AUTHORIZE_URL = "https://oauth.vk.com/authorize"
BLANK_REDIRECT = "https://oauth.vk.com/blank.html"
"""Штатный адрес возврата для Standalone-приложения. Токен приезжает во фрагменте адреса
на страницу-заглушку VK, и забрать его оттуда может только человек — скопировав адрес.

Свой `http://localhost` тоже работает, но лишь когда он вписан в настройки приложения;
у свежесозданного Standalone его там нет, и VK отвечает «redirect_uri не разрешён»."""
LOCAL_REDIRECT = "http://localhost"
API_VERSION = "5.199"

DEFAULT_SCOPE = "wall,photos,video,groups,offline"
"""`offline` — токен без срока жизни: у обычного OAuth это работает, в отличие от VK ID,
где `offline` даёт лишь право обновляться. Остальное — ровно то, ради чего личный токен
и нужен: запись на стену и загрузка фото и видео."""

_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Токен получен</title>
<p id="s">Забираю токен…</p>
<script>
// Фрагмент адреса (#access_token=...) на сервер не уходит — забираем его здесь и
// отправляем отдельным запросом. Иначе приёмник получил бы пустые параметры.
var h = location.hash.slice(1);
if (h) {
  fetch('/save?' + h).then(function () {
    document.getElementById('s').textContent = 'Готово, окно можно закрыть.';
  });
} else {
  document.getElementById('s').textContent =
    'Токена в адресе нет — посмотрите вывод скрипта.';
}
</script>
"""


class _TokenCatcher(http.server.BaseHTTPRequestHandler):
    token: str | None = None
    user_id: str | None = None
    expires_in: str | None = None

    def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/save":
            query = urllib.parse.parse_qs(parsed.query)
            type(self).token = (query.get("access_token") or [None])[0]
            type(self).user_id = (query.get("user_id") or [None])[0]
            type(self).expires_in = (query.get("expires_in") or [None])[0]
            body = b"ok"
        else:
            body = _PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Молчим: в строке лога был бы сам токен."""


def _wait_for_token(port: int, timeout: int) -> str:
    """Держим приёмник, пока не придёт токен или не выйдет время.

    Обслуживать один запрос нельзя: сначала браузер забирает страницу, и только вторым
    запросом приходит сам токен. Плюс браузер стучится и сам по себе — за favicon."""
    server = http.server.HTTPServer(("127.0.0.1", port), _TokenCatcher)
    server.timeout = 1

    def serve() -> None:
        deadline = time.monotonic() + timeout
        while _TokenCatcher.token is None and time.monotonic() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    thread.join(timeout + 2)
    server.server_close()
    if not _TokenCatcher.token:
        raise SystemExit(
            "Токен не пришёл. Проверьте, что в приложении разрешён redirect "
            f"{REDIRECT_URI} и что вход завершён."
        )
    return _TokenCatcher.token


REMOTE_SAVE = r"""
import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path

task = json.loads(base64.b64decode(DATA).decode())

env_path = Path(task["env_path"])
if not env_path.parent.is_dir():
    print("ОШИБКА: каталога", env_path.parent, "нет — путь приехал искажённым")
    raise SystemExit(2)

token = task["token"]


def call(method, **params):
    params.update({"access_token": token, "v": "5.199"})
    url = "https://api.vk.com/method/%s?%s" % (method, urllib.parse.urlencode(params))
    data = json.loads(urllib.request.urlopen(url, timeout=20).read().decode())
    if "error" in data:
        return "ОШИБКА %s: %s" % (data["error"]["error_code"], data["error"]["error_msg"])
    return data["response"]

# Проверяем ДО записи и именно на сервере: токен может быть привязан к IP выдачи, и
# тогда он бесполезен здесь при том, что дома работает (так вело себя VK ID 08.09).
who = call("users.get")
if isinstance(who, str):
    print("Токен на сервере НЕ работает:", who)
    raise SystemExit(3)
print("Токен работает на сервере: аккаунт", who[0]["id"], who[0].get("first_name", ""))

# Живость проверяем действием, ради которого токен и нужен: `users.get` отвечает
# нормально и у забаненного, и у не-админа (грабли 2026-08-03 и 2026-08-04).
upload = call("photos.getWallUploadServer", group_id=int(task["probe_group"]))
if isinstance(upload, str):
    print("Загрузка медиа НЕ доступна:", upload)
else:
    print("Загрузка медиа доступна.")

names = [n for n in task["vars"]]
lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
written = set()
for index, line in enumerate(lines):
    name = line.split("=", 1)[0].strip()
    if name in names:
        lines[index] = name + "=" + token
        written.add(name)
for name in names:
    if name not in written:
        lines.append(name + "=" + token)
env_path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
print("Записано в", env_path, "переменных:", ", ".join(names))
"""


def _ssh_binary() -> str:
    """Виндовый OpenSSH: msys-ssh из Git Bash не находит ключ, когда имя пользователя
    Windows написано кириллицей (разобрано 2026-08-14)."""
    windows_ssh = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
    return str(windows_ssh) if windows_ssh.exists() else "ssh"


def _save_on_host(host: str, python_bin: str, task: dict) -> None:
    # `python -` читает со stdin ПРОГРАММУ, а не данные: JSON, отправленный туда,
    # интерпретатор съедает как выражение и молча выходит с нулём. Поэтому данные
    # вшиваем в текст программы строкой base64 — в `ps` они при этом не видны.
    payload = base64.b64encode(json.dumps(task).encode()).decode()
    program = 'DATA = "' + payload + '"\n' + REMOTE_SAVE
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
        raise SystemExit(f"Сохранить на сервере не удалось: {errors or output}")


def _token_from_file(path: Path) -> str:
    """Достаёт токен из файла, куда владелец вставил адрес возврата или сам токен.

    Принимаем и целый адрес (`https://oauth.vk.com/blank.html#access_token=...`), и голое
    значение: человек копирует из адресной строки как получится, и разбирать это должен
    скрипт, а не человек. Файл после чтения удаляем — токен не должен оставаться на диске.
    """
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    token = raw
    if "access_token=" in raw:
        tail = raw.split("access_token=", 1)[1]
        token = tail.split("&", 1)[0].strip()
    token = token.strip().strip('"').strip("'")
    if not token or len(token) < 40:
        raise SystemExit(
            f"В {path} нет похожего на токен значения. Вставьте адрес целиком — "
            "от https:// до конца строки."
        )
    try:
        path.unlink()
    except OSError:
        print(f"⚠️ Не удалось удалить {path} — сотрите файл сами, там лежит токен.")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default="54733601", help="ID приложения VK")
    parser.add_argument(
        "--from-file",
        default="",
        help="Файл, куда вставлен адрес возврата с токеном. Для способов, где возврат "
        "идёт не на localhost (blank.html, vkhost и подобные)",
    )
    parser.add_argument("--scope", default=DEFAULT_SCOPE, help="Запрашиваемые права")
    parser.add_argument(
        "--var",
        action="append",
        default=None,
        help="Имя переменной в .env. Можно повторить: один токен под несколькими именами",
    )
    parser.add_argument("--exchange-host", default="", help="Сервер, куда записать токен")
    parser.add_argument("--remote-env", default="/opt/news-rewriter/.env")
    parser.add_argument("--remote-python", default="/opt/news-rewriter/venv/bin/python")
    parser.add_argument(
        "--probe-group",
        default="240120678",
        help="Сообщество, на котором проверяется загрузка медиа",
    )
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    variables = args.var or ["VK_PHOTO_UPLOAD_TOKEN"]

    if args.from_file:
        token = _token_from_file(Path(args.from_file))
        print("Токен прочитан из файла, файл удалён.")
    else:
        params = {
            "client_id": args.client_id,
            "redirect_uri": LOCAL_REDIRECT,
            "display": "page",
            "scope": args.scope,
            "response_type": "token",
            "v": API_VERSION,
        }
        url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

        print("Открываю вход VK. Войдите тем аккаунтом, от имени которого публикуем.")
        print(f"Если браузер не открылся сам — адрес:\n{url}\n")
        print(
            "⚠️ Если VK ответит «redirect_uri не разрешён» — приложение не Standalone "
            f"или в нём не прописан {LOCAL_REDIRECT}. Тогда берите токен любым другим "
            "способом и передайте адрес через --from-file."
        )
        webbrowser.open(url)

        token = _wait_for_token(args.port, args.timeout)
        print("Токен получен, аккаунт:", _TokenCatcher.user_id)

    if not args.exchange_host:
        raise SystemExit(
            "Не указан --exchange-host: печатать токен на экран не будем. "
            "Укажите сервер, куда его записать."
        )

    _save_on_host(
        args.exchange_host,
        args.remote_python,
        {
            "token": token,
            "env_path": args.remote_env,
            "vars": variables,
            "probe_group": args.probe_group,
        },
    )


if __name__ == "__main__":
    main()
