import socks

from app.core.monitoring.telegram_fetcher import detect_telethon_proxy


def test_detect_telethon_proxy_parses_host_and_port(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10801")
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    result = detect_telethon_proxy()

    assert result == (socks.HTTP, "127.0.0.1", 10801)


def test_detect_telethon_proxy_none_when_unset(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    assert detect_telethon_proxy() is None
