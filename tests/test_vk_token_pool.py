"""Общий пул личных VK-токенов: равномерность, каповка, кулдауны, fail-open.

Сеть здесь не трогаем — `_fetch_account_id` подменяется, иначе тесты били бы по VK API.
"""
from __future__ import annotations

import datetime

import pytest

from app.core.publishing import vk_token_pool
from app.core.publishing.vk_token_pool import VkTokenPool

NOW = datetime.datetime(2026, 8, 3, 12, 0, 0)
GAP = datetime.timedelta(minutes=vk_token_pool.MIN_GAP_MINUTES)


def picks_spread_over_time(pool, count: int, *, start=NOW) -> list[str]:
    """Серия выборов с соблюдением зазора между загрузками — так работает реальный
    поток публикаций. Без сдвига времени сработала бы защита от всплеска."""
    return [pool.pick(now=start + GAP * i).env_name for i in range(count)]


@pytest.fixture
def pool_factory(tmp_path, monkeypatch):
    """Пул из N токенов. Фейковый аккаунт стабилен по токену, но НЕ содержит его текста —
    иначе тесты на утечку секрета проверяли бы сами себя."""
    accounts: dict[str, str] = {}
    monkeypatch.setattr(
        vk_token_pool,
        "_fetch_account_id",
        lambda token: accounts.setdefault(token, f"acc-{len(accounts) + 1}"),
    )

    def make(tokens: dict[str, str], *, daily_cap: int = 12) -> VkTokenPool:
        for name, value in tokens.items():
            monkeypatch.setenv(name, value)
        return VkTokenPool(
            list(tokens),
            db_path=tmp_path / "pool.db",
            env_file=tmp_path / "missing.env",
            daily_cap=daily_cap,
        )

    return make


def test_distributes_evenly_across_tokens(pool_factory):
    pool = pool_factory({"T1": "a", "T2": "b", "T3": "c"})

    picks = picks_spread_over_time(pool, 9)

    assert {name: picks.count(name) for name in ("T1", "T2", "T3")} == {"T1": 3, "T2": 3, "T3": 3}


def test_spread_never_exceeds_one_publication(pool_factory):
    """Главное требование заказчика: одинаковое количество публикаций на токен."""
    pool = pool_factory({"T1": "a", "T2": "b", "T3": "c"})

    picks = picks_spread_over_time(pool, 10)

    counts = [picks.count(name) for name in ("T1", "T2", "T3")]
    assert max(counts) - min(counts) <= 1


def test_counters_are_shared_between_separate_pool_objects(pool_factory):
    """Разные софты — разные процессы и разные объекты пула, но счётчик один."""
    first = pool_factory({"T1": "a", "T2": "b"})
    second = VkTokenPool(
        ["T1", "T2"], db_path=first.db_path, env_file=first.env_file, daily_cap=first.daily_cap
    )

    first.pick(now=NOW)
    chosen = second.pick(now=NOW)

    assert chosen.env_name == "T2"  # второй процесс видит, что T1 уже занят


def test_daily_cap_stops_rotation(pool_factory):
    pool = pool_factory({"T1": "a"}, daily_cap=2)

    assert pool.pick(now=NOW) is not None
    assert pool.pick(now=NOW + GAP) is not None
    assert pool.pick(now=NOW + GAP * 2) is None


def test_counter_resets_next_day(pool_factory):
    pool = pool_factory({"T1": "a"}, daily_cap=1)
    pool.pick(now=NOW)

    assert pool.pick(now=NOW) is None
    assert pool.pick(now=NOW + datetime.timedelta(days=1)) is not None


def test_error_puts_account_on_cooldown(pool_factory):
    pool = pool_factory({"T1": "a", "T2": "b"})
    lease = pool.pick(now=NOW)

    pool.record_error(lease, now=NOW)

    later = NOW + datetime.timedelta(minutes=30)
    assert pool.pick(now=later).env_name == "T2"


def test_block_cooldown_lasts_a_day(pool_factory):
    pool = pool_factory({"T1": "a"})
    lease = pool.pick(now=NOW)

    pool.record_error(lease, blocked=True, now=NOW)

    assert pool.pick(now=NOW + datetime.timedelta(hours=12)) is None
    assert pool.pick(now=NOW + datetime.timedelta(hours=25)) is not None


def test_same_account_under_two_names_shares_one_counter(pool_factory):
    """Минусы зовут токен VK_TOKEN, Музыка — VK_USER_TOKEN. Аккаунт один, счётчик один."""
    pool = pool_factory({"VK_TOKEN": "same", "VK_USER_TOKEN": "same"}, daily_cap=2)

    assert pool.pick(now=NOW) is not None
    assert pool.pick(now=NOW + GAP) is not None
    assert pool.pick(now=NOW + GAP * 2) is None


def test_missing_env_variables_are_skipped(pool_factory):
    pool = pool_factory({"T1": "a"})
    pool.pool_env_names = ["T_ABSENT", "T1"]

    assert pool.pick(now=NOW).env_name == "T1"


def test_empty_pool_returns_none(pool_factory):
    pool = pool_factory({"T1": "a"})
    pool.pool_env_names = ["NOTHING_HERE"]

    assert pool.pick(now=NOW) is None


def test_token_value_never_leaks_into_repr(pool_factory):
    pool = pool_factory({"T1": "supersecret"})

    assert "supersecret" not in repr(pool.pick(now=NOW))


def test_broken_storage_falls_back_to_first_token(pool_factory, tmp_path):
    """Fail-open: сломанное хранилище не должно останавливать публикацию."""
    pool = pool_factory({"T1": "a", "T2": "b"})
    blocker = tmp_path / "blocked"
    blocker.mkdir()
    pool.db_path = blocker  # каталог вместо файла БД → sqlite3.Error

    lease = pool.pick(now=NOW)

    assert lease is not None and lease.env_name == "T1"


def test_env_file_supplies_tokens_absent_from_process_env(tmp_path, monkeypatch):
    monkeypatch.setattr(vk_token_pool, "_fetch_account_id", lambda token: f"acc-{token}")
    monkeypatch.delenv("SHARED_T1", raising=False)
    env_file = tmp_path / "vk-tokens.env"
    env_file.write_text('SHARED_T1="from-file"\n# комментарий\n', encoding="utf-8")
    pool = VkTokenPool(["SHARED_T1"], db_path=tmp_path / "pool.db", env_file=env_file)

    assert pool.pick(now=NOW).token == "from-file"


def test_process_env_wins_over_shared_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vk_token_pool, "_fetch_account_id", lambda token: f"acc-{token}")
    monkeypatch.setenv("SHARED_T1", "from-env")
    env_file = tmp_path / "vk-tokens.env"
    env_file.write_text("SHARED_T1=from-file\n", encoding="utf-8")
    pool = VkTokenPool(["SHARED_T1"], db_path=tmp_path / "pool.db", env_file=env_file)

    assert pool.pick(now=NOW).token == "from-env"


def test_unresolvable_account_still_rotates(tmp_path, monkeypatch):
    """VK недоступен → ключом становится хэш токена, балансировка продолжается."""
    monkeypatch.setattr(vk_token_pool, "_fetch_account_id", lambda token: None)
    monkeypatch.setenv("T1", "a")
    monkeypatch.setenv("T2", "b")
    pool = VkTokenPool(
        ["T1", "T2"], db_path=tmp_path / "pool.db", env_file=tmp_path / "missing.env"
    )

    picks = picks_spread_over_time(pool, 4)

    assert picks.count("T1") == 2 and picks.count("T2") == 2


def test_report_shows_load_without_exposing_tokens(pool_factory):
    pool = pool_factory({"T1": "supersecret", "T2": "b"})
    pool.pick(now=NOW)

    report = pool.report(now=NOW)

    assert "T1" in report and "1/12" in report
    assert "supersecret" not in report


def test_burst_on_one_account_is_impossible(pool_factory):
    """Главный урок бана 2026-07-02: 12 публикаций за секунду с одного аккаунта.
    Пул обязан сделать это физически невозможным."""
    pool = pool_factory({"T1": "a"})

    assert pool.pick(now=NOW) is not None
    assert pool.pick(now=NOW + datetime.timedelta(seconds=1)) is None
    assert pool.pick(now=NOW + datetime.timedelta(minutes=1)) is None
    assert pool.pick(now=NOW + GAP) is not None


def test_simultaneous_softs_land_on_different_accounts(pool_factory):
    """Минусы и Музыка могут дёрнуть публикацию в одну секунду — они должны разойтись
    по разным аккаунтам, а не сложиться во всплеск на одном."""
    pool = pool_factory({"T1": "a", "T2": "b"})

    first = pool.pick(now=NOW)
    second = pool.pick(now=NOW)

    assert first.env_name != second.env_name
    assert pool.pick(now=NOW) is None  # третьего свободного аккаунта нет — ждём зазор
