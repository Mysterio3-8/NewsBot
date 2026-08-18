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


def test_failed_exit_is_skipped_on_retry(tmp_path, monkeypatch):
    """Выход, подведший на СКАЧИВАНИИ, пропускается — даже если метаданные он отдаёт.

    CDN отвечает `403 Forbidden` отдельно от плеера: 15.08 выход прошёл проверку и всё
    равно не отдал данные. Без пропуска повтор упирался бы в тот же выход."""
    repo = _repo(tmp_path)
    monkeypatch.setenv(PROXY_PORTS_ENV, "10813,10811")
    repo.set_setting(LAST_GOOD_PROXY_KEY, "socks5://127.0.0.1:10813")

    picked = pick_working_proxy(
        repo, probe=lambda proxy: True, exclude="socks5://127.0.0.1:10813"
    )

    assert picked == "socks5://127.0.0.1:10811"


def test_single_exit_leaves_nothing_to_retry_with(tmp_path, monkeypatch):
    """Запасного выхода нет — честно None, чтобы вызывающий не повторял впустую."""
    repo = _repo(tmp_path)
    monkeypatch.setenv(PROXY_PORTS_ENV, "10813")

    assert pick_working_proxy(
        repo, probe=lambda proxy: True, exclude="socks5://127.0.0.1:10813"
    ) is None


# --- проверка ДАННЫХ, а не только метаданных (2026-08-18) --------------------


class _Response:
    """Ответ CDN: код и сколько байт реально отдал."""

    def __init__(self, status_code: int, payload: bytes):
        self.status_code = status_code
        self.raw = _Raw(payload)

    def close(self):
        pass


class _Raw:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size, decode_content=True):
        return self._payload[:size]


def test_exit_that_cuts_data_after_two_megabytes_is_rejected(monkeypatch):
    """🔴 Живой случай 18.08: пять выходов отдавали форматы безупречно, а данные резали
    ровно на 2 МБ — фильм не качался ни через один, и проверка «жив ли выход» врала."""
    from app.core.video import proxy_rotation

    monkeypatch.setattr(
        proxy_rotation, "_probe_url", lambda formats: "https://cdn/video"
    )
    captured = {}

    def fake_get(url, headers=None, proxies=None, timeout=None, stream=None):
        captured["headers"] = headers
        return _Response(206, b"x" * (2 * 1024 * 1024))  # ровно два мегабайта и всё

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    assert not proxy_rotation._data_flows("socks5://127.0.0.1:10817", "https://cdn/video")
    assert captured["headers"]["Range"].startswith("bytes=0-")


def test_exit_that_delivers_the_whole_slice_passes(monkeypatch):
    from app.core.video import proxy_rotation

    import requests

    monkeypatch.setattr(
        requests, "get",
        lambda *a, **kw: _Response(206, b"x" * proxy_rotation.DATA_PROBE_BYTES),
    )

    assert proxy_rotation._data_flows("socks5://127.0.0.1:10817", "https://cdn/video")


def test_forbidden_on_data_is_not_a_working_exit(monkeypatch):
    from app.core.video import proxy_rotation

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Response(403, b""))

    assert not proxy_rotation._data_flows("socks5://127.0.0.1:10817", "https://cdn/video")


def test_probe_slice_is_bigger_than_the_observed_cut():
    """Порог пробы обязан быть ВЫШЕ реза, иначе проверка врёт ровно в том случае,
    ради которого написана."""
    from app.core.video.proxy_rotation import DATA_PROBE_BYTES

    assert DATA_PROBE_BYTES > 2 * 1024 * 1024


def test_lightest_stream_is_chosen_for_the_probe():
    """Проба стоит трафика, а режет CDN одинаково и аудио, и видео."""
    from app.core.video.proxy_rotation import _probe_url

    formats = [
        {"url": "https://cdn/heavy", "tbr": 1642},
        {"url": "https://cdn/light", "tbr": 49},
        {"tbr": 1},  # без ссылки — не кандидат
    ]

    assert _probe_url(formats) == "https://cdn/light"


def test_no_url_means_no_probe():
    from app.core.video.proxy_rotation import _data_flows

    assert not _data_flows("socks5://127.0.0.1:10817", "")
