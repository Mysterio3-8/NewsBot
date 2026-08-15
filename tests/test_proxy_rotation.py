"""Ротация прокси-выходов: протухший IP чинится сам.

Барьер YouTube «Sign in to confirm you're not a bot» зависит от IP — 2026-08-14 живой
перебор пяти VPN-выходов показал, что четыре закрыты, а шведский отдаёт видео без куки.
Прибивать рабочий выход руками — мина: IP протухает за недели, и чинить пришлось бы
человеку, узнав о поломке по пустой стене.
"""
from app.core.video.proxy_rotation import (
    LAST_GOOD_PROXY_KEY,
    PROXY_PORTS_ENV,
    pick_working_proxy,
    proxy_candidates,
)
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "proxy.db")
    init_db(engine)
    return Repository(engine)


def test_candidates_put_last_good_first(monkeypatch):
    """Удачный в прошлый раз проверяется первым — он почти всегда и нужен, а лишние
    пробы по закрытым выходам это лишние запросы к YouTube."""
    monkeypatch.setenv(PROXY_PORTS_ENV, "10811,10812,10813")

    order = proxy_candidates("socks5://127.0.0.1:10813")

    assert order[0] == "socks5://127.0.0.1:10813"
    assert set(order) == {f"socks5://127.0.0.1:{p}" for p in (10811, 10812, 10813)}


def test_switches_to_the_first_working_exit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv(PROXY_PORTS_ENV, "10811,10812,10813")
    repo.set_setting(LAST_GOOD_PROXY_KEY, "socks5://127.0.0.1:10811")
    tried: list[str] = []

    def probe(proxy):
        tried.append(proxy)
        return proxy.endswith("10813")

    assert pick_working_proxy(repo, probe=probe) == "socks5://127.0.0.1:10813"
    # новый рабочий выход запомнен — следующий запуск начнёт с него
    assert repo.get_setting(LAST_GOOD_PROXY_KEY) == "socks5://127.0.0.1:10813"
    assert tried[0] == "socks5://127.0.0.1:10811"


def test_all_exits_closed_means_direct(tmp_path, monkeypatch):
    """Прямой путь иногда проходит, а «не пробовать вовсе» гарантирует сутки без фильма."""
    repo = _repo(tmp_path)
    monkeypatch.setenv(PROXY_PORTS_ENV, "10811,10812")

    assert pick_working_proxy(repo, probe=lambda proxy: False) is None


def test_without_port_list_falls_back_to_single_proxy(tmp_path, monkeypatch):
    """Ротация не настроена — работает обычный YT_PROXY, поведение прежнее."""
    repo = _repo(tmp_path)
    monkeypatch.delenv(PROXY_PORTS_ENV, raising=False)
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")

    assert pick_working_proxy(repo, probe=lambda proxy: False) == "socks5://127.0.0.1:10808"


def test_working_exit_is_not_rewritten(tmp_path, monkeypatch):
    """Лишняя запись в настройки на каждом прогоне — лишний шум в БД."""
    repo = _repo(tmp_path)
    monkeypatch.setenv(PROXY_PORTS_ENV, "10813")
    repo.set_setting(LAST_GOOD_PROXY_KEY, "socks5://127.0.0.1:10813")

    assert pick_working_proxy(repo, probe=lambda proxy: True) == "socks5://127.0.0.1:10813"
    assert repo.get_setting(LAST_GOOD_PROXY_KEY) == "socks5://127.0.0.1:10813"
