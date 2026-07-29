import json
from types import SimpleNamespace

from app import soundcloud_panel as panel


def _record(**overrides):
    base = {
        "soft_id": "p_music",
        "project_path": "/opt/yt-vk-publisher",
        "config_json": json.dumps({"soundcloud": True}),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_soft_with_flag_and_path_supports_soundcloud():
    assert panel.supports_soundcloud(_record())


def test_soft_without_flag_is_skipped():
    assert not panel.supports_soundcloud(_record(config_json="{}"))


def test_soft_without_project_path_is_skipped():
    assert not panel.supports_soundcloud(_record(project_path=None))


def test_broken_config_json_does_not_crash_the_bot():
    assert not panel.supports_soundcloud(_record(config_json="{не json"))


def test_missing_record_is_skipped():
    assert not panel.supports_soundcloud(None)


def test_enqueue_result_mentions_title_and_track_count():
    text = panel.render_enqueue_result(
        {"ok": True, "title": "Dragonborn", "tracks": 12, "ahead_in_queue": 0}
    )

    assert "Dragonborn" in text
    assert "12" in text


def test_enqueue_result_warns_about_the_wait_when_queue_is_busy():
    text = panel.render_enqueue_result(
        {"ok": True, "title": "Second", "tracks": 5, "ahead_in_queue": 2}
    )

    assert "Перед ним в очереди: 2" in text


def test_enqueue_error_is_shown_to_the_user():
    text = panel.render_enqueue_result({"ok": False, "error": "Это не ссылка на SoundCloud"})

    assert "Это не ссылка на SoundCloud" in text


def test_status_reports_idle_queue():
    text = panel.render_status({"ok": True, "pending_albums": 0, "active": None})

    assert "ничего не публикуется" in text


def test_status_reports_progress_and_next_moment():
    text = panel.render_status(
        {
            "ok": True,
            "pending_albums": 1,
            "active": {
                "title": "Dragonborn",
                "status": "publishing",
                "tracks_total": 12,
                "tracks_left": 7,
                "next_post_at": "2026-07-28T18:30:00",
            },
        }
    )

    assert "7 из 12" in text
    assert "28.07 18:30" in text
    assert "Ждут своей очереди: 1" in text


def test_missing_cli_reports_path_instead_of_crashing(tmp_path):
    result = panel._run_cli(str(tmp_path), ["status"], timeout=5)

    assert result["ok"] is False
    assert "CLI софта не найден" in result["error"]
