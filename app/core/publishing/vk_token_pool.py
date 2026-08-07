"""Общий пул личных (user) VK-токенов — один на ВСЕ софты сервера.

Зачем: VK банит аккаунт за ВСПЛЕСК активности, а не за суточный объём — инцидент
2026-07-02 это 12 публикаций за секунду с одного токена, при том что 30+ загрузок в
сутки тот же аккаунт переносил штатно. Четыре канала на этом сервере грузят медиа
личными токенами независимо друг от друга, поэтому ни один софт в одиночку не видит
реальной плотности запросов к аккаунту.

Пул решает обе задачи разом: счётчики вынесены в общий SQLite-файл, загрузка уходит
наименее нагруженному сегодня аккаунту (разброс за сутки не больше единицы), и аккаунт,
публиковавший меньше MIN_GAP_MINUTES назад, из выбора исключается — всплеск на одном
токене становится невозможен даже при одновременных запросах от разных софтов.

Секретов здесь не хранится: в БД лежат `user_id` и хэш токена, сам токен приходит из
окружения либо из общего файла секретов и никогда не логируется.

ВЕНДОРЕННЫЙ ФАЙЛ. Копии живут в NewsSoft/app/core/publishing/, MinusSoft/ и
TelegramMusicSoft/app/. При правке синхронизировать все три (см. SPEC_TOKEN_BALANCER.md).
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/var/lib/vk-token-balancer/state.db"
DEFAULT_ENV_FILE = "/etc/vk-tokens.env"

DEFAULT_DAILY_CAP = 17
"""Загрузок на ОДИН аккаунт в сутки.

Важно понимать, за что реально прилетает бан. Инцидент 2026-07-02 — это 12 публикаций
**за секунду**, то есть всплеск, а не объём: владелец штатно держал 30+ загрузок в сутки
на одном аккаунте без последствий. Поэтому суточный кап здесь — грубый предохранитель,
а настоящую защиту даёт MIN_GAP_MINUTES ниже."""

MIN_GAP_MINUTES = 40
"""Минимальный зазор между двумя загрузками ОДНОГО аккаунта.

Это главный антибан-механизм пула: он физически не даёт собрать всплеск, даже если
несколько софтов одновременно решат что-то опубликовать. Аккаунт, использованный
меньше MIN_GAP_MINUTES назад, пропускается, и загрузка уходит следующему.

С 2026-08-06 зазор работает ещё и как ЕДИНОЕ РАСПИСАНИЕ на все четыре софта. ТЗ
владельца: «чтобы посты в других группах никогда не пересекались, интервал на 24 часа
растягивай». Рабочий личный аккаунт остался один, счётчики у софтов общие, поэтому
зазор на аккаунт = зазор между ЛЮБЫМИ двумя публикациями с медиа на сервере.

Арифметика: Новости 5 + Кино 6 (фильм + 2 клипа + 3 поста) + Минусы 2 + Треки 5 = 18
публикаций в сутки, то есть в среднем одна раз в 80 минут.

Зазор — это ПОЛ, а не расписание: раздвигают публикации по суткам собственные интервалы
софтов (Новости 150–215, Кино 330–460, Треки 240–330 мин). Роль пола — запретить всплеск
и наложение. Поэтому он должен быть заметно НИЖЕ среднего интервала, иначе запаса нет:
при 60 мин на 18 публикаций свободных слотов всего 24 в сутки, и любая случайная кучность
роняет публикацию в отложенные — ровно это владелец видел как «в кино мало, в новостях
мало» 05–07.08.

40 минут дают 36 слотов на 18 публикаций — двукратный запас, при этом наложение
по-прежнему невозможно: две записи физически не выйдут ближе чем через 40 минут.
Растёт объём — зазор снижать дальше; 36 публикаций в сутки потребуют уже 40 ровно.

⚠️ Значение задаётся ещё и конфигами Минусов и Треков (`vk.token_min_gap_minutes`) —
менять надо во ВСЕХ трёх местах, иначе софты разъедутся по расписанию."""

COOLDOWN_MINUTES_AFTER_ERROR = 90
COOLDOWN_MINUTES_AFTER_BLOCK = 24 * 60
"""`5 / user is blocked` — это бан, а не троттлинг: убираем аккаунт из ротации на сутки."""

_BUSY_TIMEOUT_MS = 5000


class TokenLease:
    """Выданный на одну загрузку токен. `account_key` нужен, чтобы сообщить об ошибке."""

    __slots__ = ("env_name", "token", "account_key")

    def __init__(self, env_name: str, token: str, account_key: str) -> None:
        self.env_name = env_name
        self.token = token
        self.account_key = account_key

    def __repr__(self) -> str:  # токен наружу не отдаём даже в отладке
        return f"TokenLease(env_name={self.env_name!r}, account_key={self.account_key!r})"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def read_env_file(path: str | Path) -> dict[str, str]:
    """Разбор общего файла секретов. Нет файла или он нечитаем — пустой словарь:
    значения всё равно могут быть в окружении процесса."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_tokens(pool_env_names: list[str], env_file: str | Path) -> list[tuple[str, str]]:
    """Пары (имя переменной, токен) в порядке пула. Окружение процесса приоритетнее
    общего файла — софт может переопределить токен у себя. Незаданные имена молча
    пропускаются: пул сокращается, публикация не падает."""
    shared = read_env_file(env_file)
    resolved = []
    for name in pool_env_names:
        value = os.environ.get(name) or shared.get(name)
        if value:
            resolved.append((name, value))
    return resolved


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS token_identity (
            token_hash  TEXT PRIMARY KEY,
            account_key TEXT NOT NULL,
            resolved_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS token_usage (
            account_key   TEXT PRIMARY KEY,
            day           TEXT NOT NULL,
            used_today    INTEGER NOT NULL DEFAULT 0,
            used_total    INTEGER NOT NULL DEFAULT 0,
            cooling_until TEXT,
            last_used_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS token_grants (
            granted_at  TEXT NOT NULL,
            account_key TEXT NOT NULL,
            caller      TEXT NOT NULL
        );
        """
    )
    # Самолечащаяся миграция: last_used_at добавлен позже, у ранних установок его нет.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)")}
    if "last_used_at" not in columns:
        conn.execute("ALTER TABLE token_usage ADD COLUMN last_used_at TEXT")
    conn.commit()
    return conn


def _record_grant(conn: sqlite3.Connection, account_key: str, caller: str, now: datetime.datetime) -> None:
    """Журнал выдач: КТО занял слот.

    Без него софты слепы друг к другу. 07.08 счётчик показал 20 выдач за сутки, а в
    логах софтов нашлись только 4 — остальные 16 ушли неизвестно куда, и понять, почему
    Минусы не получают слот, было нечем. Пишем в общий файл, а не в лог каждого софта:
    у них разные логгеры и разные каталоги."""
    conn.execute(
        "INSERT INTO token_grants (granted_at, account_key, caller) VALUES (?, ?, ?)",
        (now.isoformat(), account_key, caller),
    )
    # Журнал нужен для разбора «за последние сутки», вечно копить его незачем.
    conn.execute(
        "DELETE FROM token_grants WHERE granted_at < ?",
        ((now - datetime.timedelta(days=3)).isoformat(),),
    )


def _fetch_account_id(token: str) -> str | None:
    """user_id токена через users.get. Импорт requests внутри функции — модуль обязан
    работать и там, где библиотеки нет (тогда ключом станет хэш токена)."""
    try:
        import requests
    except ImportError:
        return None
    try:
        response = requests.get(
            "https://api.vk.com/method/users.get",
            params={"access_token": token, "v": "5.199"},
            timeout=20,
        )
        payload = response.json()
    except Exception as exc:  # сеть/парсинг — не повод ронять публикацию
        logger.warning("Пул токенов: не удалось определить аккаунт токена: %s", exc)
        return None
    items = payload.get("response") or []
    if not items or not isinstance(items, list):
        return None
    user_id = items[0].get("id")
    return str(user_id) if user_id is not None else None


class VkTokenPool:
    """Пул личных токенов с общими на весь сервер счётчиками.

    Любая ошибка хранилища — это WARNING и откат на первый доступный токен пула:
    антибан важен, но не важнее того, чтобы публикация вообще состоялась.
    """

    def __init__(
        self,
        pool_env_names: list[str],
        *,
        db_path: str | Path | None = None,
        env_file: str | Path | None = None,
        daily_cap: int = DEFAULT_DAILY_CAP,
        min_gap_minutes: int = MIN_GAP_MINUTES,
        caller: str = "unknown",
    ) -> None:
        self.pool_env_names = pool_env_names
        self.caller = caller
        self.db_path = db_path or os.environ.get("VK_TOKEN_POOL_DB", DEFAULT_DB_PATH)
        self.env_file = env_file or os.environ.get("VK_TOKEN_POOL_ENV_FILE", DEFAULT_ENV_FILE)
        self.daily_cap = daily_cap
        self.min_gap_minutes = min_gap_minutes

    def pick(self, *, now: datetime.datetime | None = None) -> TokenLease | None:
        """Наименее нагруженный сегодня токен; использование засчитывается сразу.

        None — все аккаунты выбрали суточный кап или сидят в кулдауне. Вызывающий код
        тогда пропускает загрузку медиа, а не берёт токен в обход лимита."""
        now = now or datetime.datetime.utcnow()
        candidates = resolve_tokens(self.pool_env_names, self.env_file)
        if not candidates:
            logger.warning("Пул токенов пуст: ни одна переменная из %s не задана", self.pool_env_names)
            return None
        try:
            return self._pick_from_store(candidates, now)
        except sqlite3.Error as exc:
            name, token = candidates[0]
            logger.warning(
                "Пул токенов: хранилище недоступно (%s) — беру первый токен пула %s", exc, name
            )
            return TokenLease(name, token, f"h:{token_hash(token)}")

    def _pick_from_store(
        self, candidates: list[tuple[str, str]], now: datetime.datetime
    ) -> TokenLease | None:
        today = now.date().isoformat()
        with _connect(self.db_path) as conn:
            keyed = [
                (name, token, self._account_key(conn, token, now)) for name, token in candidates
            ]
            conn.execute("BEGIN IMMEDIATE")
            rows = {
                row[0]: row
                for row in conn.execute(
                    "SELECT account_key, day, used_today, used_total, cooling_until, "
                    "last_used_at FROM token_usage"
                )
            }
            gap = datetime.timedelta(minutes=self.min_gap_minutes)
            best: tuple[int, int, int] | None = None
            chosen: tuple[str, str, str] | None = None
            for position, (name, token, key) in enumerate(keyed):
                row = rows.get(key)
                used_today = row[2] if row and row[1] == today else 0
                used_total = row[3] if row else 0
                if row and row[4] and now < datetime.datetime.fromisoformat(row[4]):
                    continue
                if used_today >= self.daily_cap:
                    continue
                # Всплеск — то, за что реально банят: аккаунт, публиковавший только что,
                # пропускаем, даже если он наименее нагружен за сутки.
                if row and row[5] and now - datetime.datetime.fromisoformat(row[5]) < gap:
                    continue
                rank = (used_today, used_total, position)
                if best is None or rank < best:
                    best, chosen = rank, (name, token, key)
            if chosen is None:
                conn.rollback()
                logger.warning(
                    "Пул токенов: свободных аккаунтов нет (кап %d/сутки, зазор %d мин, кулдауны)",
                    self.daily_cap,
                    self.min_gap_minutes,
                )
                return None
            name, token, key = chosen
            conn.execute(
                """
                INSERT INTO token_usage (account_key, day, used_today, used_total, last_used_at)
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    used_today = CASE WHEN token_usage.day = excluded.day
                                      THEN token_usage.used_today + 1 ELSE 1 END,
                    used_total = token_usage.used_total + 1,
                    day = excluded.day,
                    last_used_at = excluded.last_used_at
                """,
                (key, today, now.isoformat()),
            )
            _record_grant(conn, key, self.caller, now)
            conn.commit()
        logger.info(
            "Пул токенов: загрузка уходит на %s (аккаунт %s, кто: %s)", name, key, self.caller
        )
        return TokenLease(name, token, key)

    def has_free_account(self, *, now: datetime.datetime | None = None) -> bool:
        """Есть ли сейчас свободный аккаунт — БЕЗ захвата слота.

        Нужна тем, кто перед публикацией делает дорогую работу: Кино качает фильм на
        сотни мегабайт и помечает его опубликованным ДО загрузки (защита от OOM-цикла,
        2026-07-28). Если после этого токена не окажется, фильм уже помечен и больше не
        возьмётся — сутки без фильма. Дешёвая проверка заранее снимает основную массу
        таких случаев: пул занят — даже не начинаем качать.

        Гонку это не исключает (между проверкой и загрузкой слот может уйти другому
        софту), поэтому вызывающий обязан пережить отказ на самой публикации."""
        now = now or datetime.datetime.utcnow()
        candidates = resolve_tokens(self.pool_env_names, self.env_file)
        if not candidates:
            return False
        today = now.date().isoformat()
        gap = datetime.timedelta(minutes=self.min_gap_minutes)
        try:
            with _connect(self.db_path) as conn:
                rows = {
                    row[0]: row
                    for row in conn.execute(
                        "SELECT account_key, day, used_today, used_total, cooling_until, "
                        "last_used_at FROM token_usage"
                    )
                }
                for name, token in candidates:
                    row = rows.get(self._account_key(conn, token, now))
                    if row is None:
                        return True
                    if row[4] and now < datetime.datetime.fromisoformat(row[4]):
                        continue
                    if row[1] == today and row[2] >= self.daily_cap:
                        continue
                    if row[5] and now - datetime.datetime.fromisoformat(row[5]) < gap:
                        continue
                    return True
                return False
        except sqlite3.Error as exc:
            logger.warning("Пул токенов: проверка доступности не удалась (%s) — считаем занятым", exc)
            return False

    def _account_key(self, conn: sqlite3.Connection, token: str, now: datetime.datetime) -> str:
        """user_id аккаунта, закэшированный по хэшу токена. Токен принадлежит одному
        аккаунту навсегда, поэтому разрешаем один раз и больше в сеть не ходим."""
        digest = token_hash(token)
        row = conn.execute(
            "SELECT account_key FROM token_identity WHERE token_hash = ?", (digest,)
        ).fetchone()
        if row:
            return row[0]
        account_id = _fetch_account_id(token)
        if account_id is None:
            return f"h:{digest}"  # без сети работаем по хэшу, кэш не портим
        conn.execute(
            "INSERT OR REPLACE INTO token_identity (token_hash, account_key, resolved_at) "
            "VALUES (?, ?, ?)",
            (digest, account_id, now.isoformat()),
        )
        conn.commit()
        return account_id

    def record_error(
        self,
        lease: TokenLease,
        *,
        blocked: bool = False,
        now: datetime.datetime | None = None,
    ) -> None:
        """Вывести аккаунт из ротации: 90 минут при обычной ошибке VK, сутки при бане."""
        now = now or datetime.datetime.utcnow()
        minutes = COOLDOWN_MINUTES_AFTER_BLOCK if blocked else COOLDOWN_MINUTES_AFTER_ERROR
        until = (now + datetime.timedelta(minutes=minutes)).isoformat()
        try:
            with _connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO token_usage (account_key, day, used_today, used_total, cooling_until)
                    VALUES (?, ?, 0, 0, ?)
                    ON CONFLICT(account_key) DO UPDATE SET cooling_until = excluded.cooling_until
                    """,
                    (lease.account_key, now.date().isoformat(), until),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Пул токенов: не удалось записать кулдаун: %s", exc)
            return
        logger.warning(
            "Пул токенов: аккаунт %s выведен из ротации на %d мин", lease.account_key, minutes
        )

    def report(self, *, now: datetime.datetime | None = None) -> str:
        """Сводка для бота: сколько загрузок на каждом аккаунте сегодня."""
        now = now or datetime.datetime.utcnow()
        today = now.date().isoformat()
        candidates = resolve_tokens(self.pool_env_names, self.env_file)
        if not candidates:
            return "Пул токенов пуст."
        lines = []
        try:
            with _connect(self.db_path) as conn:
                for name, token in candidates:
                    key = self._account_key(conn, token, now)
                    row = conn.execute(
                        "SELECT day, used_today, cooling_until FROM token_usage WHERE account_key = ?",
                        (key,),
                    ).fetchone()
                    used = row[1] if row and row[0] == today else 0
                    cooling = bool(row and row[2] and now < datetime.datetime.fromisoformat(row[2]))
                    mark = "❄️" if cooling else ("🔴" if used >= self.daily_cap else "🟢")
                    lines.append(f"{mark} {name} (акк. {key}): {used}/{self.daily_cap}")
        except sqlite3.Error as exc:
            return f"Пул токенов: хранилище недоступно ({exc})"
        return "\n".join(lines)
