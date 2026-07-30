"""Балансер личных VK-токенов: раскидывает загрузки медиа по пулу, чтобы объём
не выжигал один токен (прецедент — бан после 12 публикаций подряд)."""
from __future__ import annotations

import datetime

from app.core.channel_settings import ChannelSettings
from app.core.publishing import token_balancer as tb

NOW = datetime.datetime(2026, 7, 30, 12, 0)
POOL = ["VK_UPLOAD_A", "VK_UPLOAD_B"]


def test_picks_first_when_all_empty():
    assert tb.pick_token(POOL, {}, now=NOW) == "VK_UPLOAD_A"


def test_picks_least_used():
    states = tb.record_use({}, "VK_UPLOAD_A", NOW)
    assert tb.pick_token(POOL, states, now=NOW) == "VK_UPLOAD_B"


def test_alternates_across_pool():
    states: dict = {}
    picks = []
    for _ in range(4):
        chosen = tb.pick_token(POOL, states, now=NOW)
        picks.append(chosen)
        states = tb.record_use(states, chosen, NOW)
    assert picks == ["VK_UPLOAD_A", "VK_UPLOAD_B", "VK_UPLOAD_A", "VK_UPLOAD_B"]


def test_respects_daily_cap_per_token():
    states: dict = {}
    for _ in range(4):
        chosen = tb.pick_token(POOL, states, now=NOW, per_token_daily_cap=2)
        states = tb.record_use(states, chosen, NOW)
    # оба токена выбрали свой лимит по 2 → больше грузить нечем
    assert tb.pick_token(POOL, states, now=NOW, per_token_daily_cap=2) is None


def test_counter_resets_next_day():
    states = tb.record_use({}, "VK_UPLOAD_A", NOW)
    tomorrow = NOW + datetime.timedelta(days=1)
    assert tb.pick_token(POOL, states, now=tomorrow, per_token_daily_cap=1) == "VK_UPLOAD_A"


def test_error_puts_token_in_cooldown():
    states = tb.record_error({}, "VK_UPLOAD_A", NOW)
    assert tb.pick_token(POOL, states, now=NOW) == "VK_UPLOAD_B"
    # после кулдауна токен возвращается в ротацию
    later = NOW + datetime.timedelta(minutes=tb.COOLDOWN_MINUTES_AFTER_ERROR + 1)
    assert tb.pick_token(POOL, states, now=later) == "VK_UPLOAD_A"


def test_all_cooling_returns_none():
    states = tb.record_error(tb.record_error({}, "VK_UPLOAD_A", NOW), "VK_UPLOAD_B", NOW)
    assert tb.pick_token(POOL, states, now=NOW) is None


def test_empty_pool_returns_none():
    assert tb.pick_token([], {}, now=NOW) is None


def test_states_roundtrip_through_json():
    states = tb.record_use({}, "VK_UPLOAD_A", NOW)
    restored = tb.load_states(tb.dump_states(states))
    assert restored["VK_UPLOAD_A"].used_today == 1


def test_load_states_tolerates_broken_json():
    assert tb.load_states("{не json") == {}
    assert tb.load_states(None) == {}
    assert tb.load_states("[]") == {}


def test_render_report_marks_state():
    states = tb.record_use({}, "VK_UPLOAD_A", NOW)
    report = tb.render_report(POOL, states, NOW, per_token_daily_cap=1)
    assert "🔴 VK_UPLOAD_A: 1/1" in report
    assert "🟢 VK_UPLOAD_B: 0/1" in report


def test_channel_settings_pool_roundtrip():
    settings = ChannelSettings(vk_upload_token_envs=POOL, vk_token_daily_cap=12)
    restored = ChannelSettings.from_json(settings.to_json())
    assert restored.vk_upload_token_envs == POOL
    assert restored.vk_token_daily_cap == 12


def test_channel_settings_omits_pool_when_empty():
    assert "vk_upload_token_envs" not in ChannelSettings().to_json()
