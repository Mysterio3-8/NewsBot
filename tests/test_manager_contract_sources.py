"""Источники внешних софтов в контракте: добавление, удаление, защита последнего.

Управление источниками из бота закрывает реальный простой: источники выгорают, и у Кино
это уже стоило двух суток. У Музыки и Минусов до сих пор не было даже такого пути.
"""
from app.manager.contract import SoftContract


def test_sources_are_stored_next_to_limits():
    contract = SoftContract(max_posts_per_day=3).with_source("запрос про рэп")

    payload = contract.to_config_dict()

    assert payload["limits"] == {"max_posts_per_day": 3}
    assert payload["sources"] == {"primary": ["запрос про рэп"]}


def test_duplicate_source_is_not_added_twice():
    contract = SoftContract().with_source("https://a")

    assert contract.with_source("https://a") is contract


def test_last_source_cannot_be_deleted():
    """Пустой список означал бы «источников нет», и софт молча перестал бы находить
    контент — владелец увидел бы это через сутки по пустой стене."""
    contract = SoftContract().with_source("https://a")

    assert contract.without_source("https://a") is contract
    assert contract.sources_primary == ("https://a",)


def test_second_stream_is_independent():
    """У Музыки два потока с разной ценой публикации: треки и сборники."""
    contract = SoftContract().with_source("трек").with_source("сборник", secondary=True)

    assert contract.sources_primary == ("трек",)
    assert contract.sources_secondary == ("сборник",)
    assert contract.to_config_dict()["sources"]["secondary"] == ["сборник"]


def test_contract_roundtrip_keeps_sources():
    contract = SoftContract(max_posts_per_day=2).with_source("https://a").with_source("https://b")

    restored = SoftContract.from_config_json(contract.to_config_json())

    assert restored.sources_primary == ("https://a", "https://b")
    assert restored.max_posts_per_day == 2


def test_summary_mentions_sources():
    contract = SoftContract(max_posts_per_day=2).with_source("https://a")

    assert "Источников: 1" in contract.render_summary()
