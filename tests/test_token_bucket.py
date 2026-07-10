import pytest

from app.core.publishing.token_bucket import TokenBucket


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds  # проспали → время идёт вперёд


def test_first_call_does_not_wait():
    fake = _FakeClock()
    bucket = TokenBucket(2.0, clock=fake.clock, sleep=fake.sleep)
    bucket.wait("VK_GROUP_TOKEN")
    assert fake.slept == []


def test_second_immediate_call_waits_min_interval():
    fake = _FakeClock()
    bucket = TokenBucket(2.0, clock=fake.clock, sleep=fake.sleep)  # 0.5с интервал
    bucket.wait("t")
    bucket.wait("t")  # сразу же → должен проспать 0.5с
    assert fake.slept == [0.5]


def test_no_wait_when_enough_time_passed():
    fake = _FakeClock()
    bucket = TokenBucket(2.0, clock=fake.clock, sleep=fake.sleep)
    bucket.wait("t")
    fake.now += 1.0  # прошла секунда — больше интервала
    bucket.wait("t")
    assert fake.slept == []


def test_different_tokens_independent():
    fake = _FakeClock()
    bucket = TokenBucket(2.0, clock=fake.clock, sleep=fake.sleep)
    bucket.wait("token_a")
    bucket.wait("token_b")  # другой токен — не ждёт
    assert fake.slept == []


def test_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        TokenBucket(0)
