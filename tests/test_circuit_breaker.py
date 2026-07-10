import datetime

from app.core.publishing.circuit_breaker import BreakerConfig, CircuitBreaker
from app.core.publishing.vk_errors import VKErrorClass
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def _breaker(repo, cooldown=(10, 15)) -> CircuitBreaker:
    # rng фиксируем на верхнюю границу — детерминированный кулдаун в тестах.
    return CircuitBreaker(
        repo,
        BreakerConfig(rate_limit_cooldown_minutes=cooldown),
        rng=lambda low, high: high,
    )


def test_closed_by_default(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo)
    assert breaker.is_open("vk", "VK_GROUP_TOKEN") is False


def test_rate_limit_opens_for_cooldown(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo, cooldown=(10, 15))
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)

    breaker.record_failure("vk", "VK_GROUP_TOKEN", VKErrorClass.RATE_LIMIT, now=now)

    # открыт спустя 14 мин, закрыт спустя 16 мин (кулдаун = 15 мин при rng→high)
    assert breaker.is_open("vk", "VK_GROUP_TOKEN", now=now + datetime.timedelta(minutes=14)) is True
    assert breaker.is_open("vk", "VK_GROUP_TOKEN", now=now + datetime.timedelta(minutes=16)) is False


def test_single_transient_error_does_not_open(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo)
    breaker.record_failure("vk", "VK_GROUP_TOKEN", VKErrorClass.TRANSIENT)
    assert breaker.is_open("vk", "VK_GROUP_TOKEN") is False


def test_transient_errors_open_after_threshold(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo)  # threshold=3 по умолчанию
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)

    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)
    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)
    assert breaker.is_open("vk", "T", now=now) is False
    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)  # 3-я подряд
    assert breaker.is_open("vk", "T", now=now) is True


def test_success_resets_transient_counter(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo)
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)

    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)
    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)
    breaker.record_success("vk", "T")  # сброс
    breaker.record_failure("vk", "T", VKErrorClass.TRANSIENT, now=now)
    assert breaker.is_open("vk", "T", now=now) is False  # снова 1 из 3, не открыт


def test_auth_blocked_opens_for_long_cooldown(tmp_path):
    repo = make_repo(tmp_path)
    breaker = CircuitBreaker(
        repo, BreakerConfig(auth_blocked_cooldown_minutes=60), rng=lambda low, high: high
    )
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)

    breaker.record_failure("vk", "VK_GROUP_TOKEN", VKErrorClass.AUTH_BLOCKED, now=now)

    assert breaker.is_open("vk", "VK_GROUP_TOKEN", now=now + datetime.timedelta(minutes=59)) is True
    assert breaker.is_open("vk", "VK_GROUP_TOKEN", now=now + datetime.timedelta(minutes=61)) is False


def test_state_persists_across_breaker_instances(tmp_path):
    """Кулдаун переживает рестарт сервиса: новый CircuitBreaker на том же repo видит
    открытую цепь (иначе рестарт AUTOSTART_SERVICE обошёл бы паузу)."""
    repo = make_repo(tmp_path)
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)
    _breaker(repo).record_failure("vk", "VK_GROUP_TOKEN", VKErrorClass.RATE_LIMIT, now=now)

    fresh = _breaker(repo)
    assert fresh.is_open("vk", "VK_GROUP_TOKEN", now=now + datetime.timedelta(minutes=5)) is True


def test_channels_isolated_by_token_env(tmp_path):
    repo = make_repo(tmp_path)
    breaker = _breaker(repo)
    now = datetime.datetime(2026, 7, 10, 12, 0, 0)

    breaker.record_failure("vk", "VK_GROUP_TOKEN_KINO", VKErrorClass.RATE_LIMIT, now=now)

    assert breaker.is_open("vk", "VK_GROUP_TOKEN_KINO", now=now) is True
    assert breaker.is_open("vk", "VK_GROUP_TOKEN", now=now) is False  # другой канал не тронут
