import shutil

import pytest

from app.config.loader import CONFIG_PATH, ConfigValidationError, update_config_section


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return dest


def test_update_config_section_persists_and_reloads(config_copy):
    updated = update_config_section(config_copy, "filters", min_score=40)
    assert updated.filters.min_score == 40

    reloaded = update_config_section(config_copy, "filters", min_score=60)
    assert reloaded.filters.min_score == 60


def test_update_config_section_rejects_invalid_value(config_copy):
    with pytest.raises(ConfigValidationError):
        update_config_section(config_copy, "filters", min_score=999)


def test_update_config_section_updates_list_fields(config_copy):
    updated = update_config_section(
        config_copy, "filters", stop_words=["скидка", "реклама"]
    )
    assert updated.filters.stop_words == ["скидка", "реклама"]
