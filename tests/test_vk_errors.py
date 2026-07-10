import pytest

from app.core.publishing.vk_errors import VKErrorClass, classify_vk_code, classify_vk_error


class _FakeApiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"[{code}] vk error")
        self.code = code


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("http error")
        self.response = _FakeResponse(status_code)


@pytest.mark.parametrize("code", [6, 9, 29])
def test_rate_limit_codes(code):
    assert classify_vk_error(_FakeApiError(code)) is VKErrorClass.RATE_LIMIT


def test_auth_blocked_code():
    assert classify_vk_error(_FakeApiError(5)) is VKErrorClass.AUTH_BLOCKED


def test_http_429_is_rate_limit():
    assert classify_vk_error(_FakeHttpError(429)) is VKErrorClass.RATE_LIMIT


def test_generic_exception_without_code_is_transient():
    """Сетевая ошибка без .code не должна fail-fast'иться как бан — она транзиентна
    и допускает ретрай (иначе сломался бы существующий ретрай VKPublisher)."""
    assert classify_vk_error(Exception("сеть недоступна")) is VKErrorClass.TRANSIENT


def test_http_5xx_is_transient():
    assert classify_vk_error(_FakeHttpError(503)) is VKErrorClass.TRANSIENT


def test_classify_code_none_is_transient():
    assert classify_vk_code(None) is VKErrorClass.TRANSIENT


def test_classify_code_direct():
    assert classify_vk_code(6) is VKErrorClass.RATE_LIMIT
    assert classify_vk_code(5) is VKErrorClass.AUTH_BLOCKED
    assert classify_vk_code(100) is VKErrorClass.TRANSIENT
