from app.core.publishing.telegram_publisher import detect_proxy_url


def test_detect_proxy_url_prefers_https_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10801")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    assert detect_proxy_url() == "http://127.0.0.1:10801"


def test_detect_proxy_url_falls_back_to_http_proxy(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    assert detect_proxy_url() == "http://127.0.0.1:9999"


def test_detect_proxy_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    assert detect_proxy_url() is None
