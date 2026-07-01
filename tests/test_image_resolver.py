from unittest.mock import Mock, patch

from app.core.images.providers.base import ImageResult
from app.core.images.resolver import resolve_to_local_file


def test_resolve_local_path_returns_as_is():
    result = ImageResult(source_provider="source", local_path="already/here.jpg")
    assert resolve_to_local_file(result, "dest.jpg") == "already/here.jpg"


def test_resolve_image_bytes_writes_to_dest(tmp_path):
    dest = tmp_path / "out.png"
    result = ImageResult(source_provider="local_ai", image_bytes=b"fake-bytes")

    resolved = resolve_to_local_file(result, dest)

    assert resolved == dest
    assert dest.read_bytes() == b"fake-bytes"


def test_resolve_url_downloads_to_dest(tmp_path):
    dest = tmp_path / "out.jpg"
    result = ImageResult(source_provider="unsplash", url="http://img")
    response = Mock(content=b"downloaded-bytes")
    response.raise_for_status = Mock()

    with patch("app.core.images.resolver.requests.get", return_value=response):
        resolved = resolve_to_local_file(result, dest)

    assert resolved == dest
    assert dest.read_bytes() == b"downloaded-bytes"
