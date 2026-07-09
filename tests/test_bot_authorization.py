"""Доступ к control-боту: основной владелец + доп. авторизованные (напарник)."""
from app.control_bot import CONTROL_BOT_EXTRA_IDS_ENV, is_authorized, register_owner
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "auth.db")
    init_db(engine)
    return Repository(engine)


def test_owner_is_authorized(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTROL_BOT_OWNER_ID", raising=False)
    monkeypatch.delenv(CONTROL_BOT_EXTRA_IDS_ENV, raising=False)
    repo = make_repo(tmp_path)
    register_owner(repo, 111)

    assert is_authorized(repo, 111) is True
    assert is_authorized(repo, 222) is False


def test_extra_authorized_id_is_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTROL_BOT_OWNER_ID", raising=False)
    monkeypatch.setenv(CONTROL_BOT_EXTRA_IDS_ENV, "7446911479")
    repo = make_repo(tmp_path)
    register_owner(repo, 111)

    assert is_authorized(repo, 7446911479) is True  # напарник
    assert is_authorized(repo, 111) is True  # владелец по-прежнему
    assert is_authorized(repo, 999) is False


def test_extra_authorized_parses_comma_list(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTROL_BOT_OWNER_ID", raising=False)
    monkeypatch.setenv(CONTROL_BOT_EXTRA_IDS_ENV, "100, 200 ,300")
    repo = make_repo(tmp_path)
    register_owner(repo, 111)

    for uid in (100, 200, 300):
        assert is_authorized(repo, uid) is True
