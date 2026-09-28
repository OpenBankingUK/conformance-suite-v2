"""Unit tests for Docker Hub tag pagination used by release workflows."""

from __future__ import annotations

import httpx
import pytest

from scripts.list_docker_hub_tags import list_tags, main

pytestmark = pytest.mark.unit


def test_lists_tags_across_all_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public tags endpoint is followed through its next-page URLs."""
    responses = iter(
        [
            httpx.Response(
                200,
                request=httpx.Request("GET", "https://hub.docker.com/tags"),
                json={
                    "results": [{"name": "2.0.0-beta.1"}, {"name": "latest"}],
                    "next": "https://hub.docker.com/v2/namespaces/openbanking/repositories/conformance-suite-v2/tags?page=2",
                },
            ),
            httpx.Response(
                200,
                request=httpx.Request("GET", "https://hub.docker.com/tags?page=2"),
                json={"results": [{"name": "2.0.0"}], "next": None},
            ),
        ]
    )
    requested_urls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: int) -> httpx.Response:
        assert headers == {"Accept": "application/json"}
        assert timeout == 30
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(httpx, "get", fake_get)

    assert list_tags() == ["2.0.0-beta.1", "latest", "2.0.0"]
    assert len(requested_urls) == 2
    assert requested_urls[1].endswith("page=2")


def test_returns_empty_when_repository_does_not_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing Docker Hub repository is an empty tag list, not a failure."""
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(404, request=httpx.Request("GET", url)),
    )

    assert list_tags() == []


def test_rejects_unexpected_pagination_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response cannot redirect pagination to an unrelated host."""
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://hub.docker.com/tags"),
        json={"results": [], "next": "https://example.com/tags?page=2"},
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: response)

    with pytest.raises(ValueError, match="unexpected pagination URL"):
        list_tags()


def test_reports_non_404_http_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Unexpected API failures make the workflow step fail explicitly."""
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(500, request=httpx.Request("GET", url)),
    )

    assert main() == 1
    assert "500" in capsys.readouterr().err
