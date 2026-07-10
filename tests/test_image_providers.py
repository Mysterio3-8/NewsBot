import base64
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.core.images.providers.google_provider import GoogleImageProvider
from app.core.images.providers.local_ai_provider import LocalAIImageProvider
from app.core.images.providers.pexels_provider import PexelsProvider
from app.core.images.providers.pixabay_provider import PixabayProvider
from app.core.images.providers.source_provider import SourceImageProvider
from app.core.images.providers.unsplash_provider import UnsplashProvider


def test_source_provider_returns_up_to_count_urls():
    provider = SourceImageProvider(["https://vk.com/a.jpg", "https://vk.com/b.jpg", "https://vk.com/c.jpg"])
    results = provider.search("query", count=2)
    assert len(results) == 2
    assert results[0].source_provider == "source"
    assert results[0].url == "https://vk.com/a.jpg"


def test_source_provider_treats_non_url_items_as_local_paths():
    """TG-фото скачиваются локально (Telethon не даёт HTTP-URL), в отличие от VK."""
    provider = SourceImageProvider(["output/tg_raw_media/photo1.jpg"])
    results = provider.search("query", count=1)
    assert results[0].url is None
    assert results[0].local_path == Path("output/tg_raw_media/photo1.jpg")


def test_unsplash_provider_maps_results():
    provider = UnsplashProvider(access_key="key")
    response = Mock()
    response.json.return_value = {"results": [{"urls": {"regular": "http://img1"}}]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.unsplash_provider.requests.get", return_value=response) as mock_get:
        results = provider.search("cats", count=1)

    assert results[0].url == "http://img1"
    assert results[0].source_provider == "unsplash"
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Client-ID key"


def test_pexels_provider_maps_results():
    provider = PexelsProvider(api_key="key")
    response = Mock()
    response.json.return_value = {"photos": [{"src": {"large": "http://img2"}}]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.pexels_provider.requests.get", return_value=response):
        results = provider.search("dogs", count=1)

    assert results[0].url == "http://img2"
    assert results[0].source_provider == "pexels"


def test_pixabay_provider_enforces_minimum_per_page_and_slices_results():
    provider = PixabayProvider(api_key="key")
    response = Mock()
    response.json.return_value = {"hits": [{"largeImageURL": f"http://img{i}"} for i in range(3)]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.pixabay_provider.requests.get", return_value=response) as mock_get:
        results = provider.search("birds", count=1)

    assert len(results) == 1
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["per_page"] == 3  # count=1 поднят до минимума API


def test_google_provider_maps_image_search_results():
    provider = GoogleImageProvider(api_key="key", cx="cx123")
    response = Mock()
    response.json.return_value = {"items": [{"link": "http://still1.jpg"}, {"link": "http://still2.jpg"}]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.google_provider.requests.get", return_value=response) as mock_get:
        results = provider.search("Ундина 2009 кадр из фильма", count=2)

    assert [r.url for r in results] == ["http://still1.jpg", "http://still2.jpg"]
    assert results[0].source_provider == "google"
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["key"] == "key"
    assert kwargs["params"]["cx"] == "cx123"
    assert kwargs["params"]["searchType"] == "image"


def test_google_provider_caps_num_at_ten():
    provider = GoogleImageProvider(api_key="key", cx="cx")
    response = Mock()
    response.json.return_value = {"items": []}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.google_provider.requests.get", return_value=response) as mock_get:
        provider.search("query", count=25)

    assert mock_get.call_args.kwargs["params"]["num"] == 10


def test_google_provider_returns_empty_list_on_request_error():
    """Бесплатный tier (100 запросов/день) легко исчерпывается — сетевая ошибка/429
    не должна ронять весь пост, только вернуть 'кадров не нашлось'."""
    provider = GoogleImageProvider(api_key="key", cx="cx")

    with patch(
        "app.core.images.providers.google_provider.requests.get",
        side_effect=requests.RequestException("429"),
    ):
        results = provider.search("query", count=1)

    assert results == []


def test_google_provider_skips_items_without_link():
    provider = GoogleImageProvider(api_key="key", cx="cx")
    response = Mock()
    response.json.return_value = {"items": [{"title": "no link here"}, {"link": "http://ok.jpg"}]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.google_provider.requests.get", return_value=response):
        results = provider.search("query", count=5)

    assert [r.url for r in results] == ["http://ok.jpg"]


def test_local_ai_provider_decodes_base64_images():
    provider = LocalAIImageProvider(host="http://localhost:7860/")
    fake_bytes = b"fake-png-bytes"
    response = Mock()
    response.json.return_value = {"images": [base64.b64encode(fake_bytes).decode()]}
    response.raise_for_status = Mock()

    with patch("app.core.images.providers.local_ai_provider.requests.post", return_value=response) as mock_post:
        results = provider.search("a news illustration", count=1)

    assert results[0].image_bytes == fake_bytes
    assert results[0].source_provider == "local_ai"
    args, _ = mock_post.call_args
    assert args[0] == "http://localhost:7860/sdapi/v1/txt2img"
