from app.db.repository import Repository, init_db, make_engine


def make_test_repository(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_create_and_list_source(tmp_path):
    repo = make_test_repository(tmp_path)

    repo.create_source(type="tg", name="Тестовый канал", url="https://t.me/test", priority=7)
    sources = repo.list_sources()

    assert len(sources) == 1
    assert sources[0].name == "Тестовый канал"
    assert sources[0].priority == 7
    assert sources[0].enabled is True


def test_list_sources_filters_by_type(tmp_path):
    repo = make_test_repository(tmp_path)
    repo.create_source(type="tg", name="TG", url="https://t.me/x")
    repo.create_source(type="vk", name="VK", url="https://vk.com/x")

    tg_sources = repo.list_sources(source_type="tg")

    assert len(tg_sources) == 1
    assert tg_sources[0].type == "tg"


def test_update_and_delete_source(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")

    repo.update_source(source.id, enabled=False)
    assert repo.list_sources()[0].enabled is False

    repo.delete_source(source.id)
    assert repo.list_sources() == []


def test_settings_roundtrip(tmp_path):
    repo = make_test_repository(tmp_path)

    assert repo.get_setting("missing_key", default="fallback") == "fallback"

    repo.set_setting("last_check", "2026-07-01T12:00:00")
    assert repo.get_setting("last_check") == "2026-07-01T12:00:00"

    repo.set_setting("last_check", "2026-07-01T13:00:00")
    assert repo.get_setting("last_check") == "2026-07-01T13:00:00"
