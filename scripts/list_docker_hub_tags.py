"""List all Docker Hub tags for the published conformance-suite repository."""

from __future__ import annotations

import json
import sys
from urllib.parse import urlsplit

import httpx

_TAGS_URL = "https://hub.docker.com/v2/namespaces/openbanking/repositories/conformance-suite-v2/tags?page_size=100"
_TAGS_PATH = "/v2/namespaces/openbanking/repositories/conformance-suite-v2/tags"


def list_tags() -> list[str]:
    """Return every tag name from the public Docker Hub tags endpoint.

    Returns:
        All tag names across the endpoint's pagination pages. A 404 on the
        endpoint is treated as an error because Docker Hub also uses 404 for
        repositories that are private or inaccessible.

    Raises:
        httpx.HTTPError: If Docker Hub cannot be reached or returns an HTTP error.
        ValueError: If Docker Hub returns an unexpected response or pagination URL.
    """
    tags: list[str] = []
    next_url: str | None = _TAGS_URL

    while next_url is not None:
        response = httpx.get(next_url, headers={"Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        payload: object = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Docker Hub returned a non-object tag response.")
        results: object = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("Docker Hub tag response has no results list.")
        for result in results:
            if not isinstance(result, dict):
                raise ValueError("Docker Hub returned a malformed tag record.")
            name: object = result.get("name")
            if not isinstance(name, str):
                raise ValueError("Docker Hub returned a tag record without a name.")
            tags.append(name)

        candidate_next: object = payload.get("next")
        if candidate_next is not None and not isinstance(candidate_next, str):
            raise ValueError("Docker Hub returned an invalid pagination URL.")
        if isinstance(candidate_next, str):
            parsed_url = urlsplit(candidate_next)
            if parsed_url.scheme != "https" or parsed_url.netloc != "hub.docker.com" or parsed_url.path != _TAGS_PATH:
                raise ValueError("Docker Hub returned an unexpected pagination URL.")
            next_url = candidate_next
        else:
            next_url = None

    return tags


def main() -> int:
    """Print newline-delimited tag names for workflow consumption."""
    try:
        for tag in list_tags():
            sys.stdout.write(f"{tag}\n")
    except (httpx.HTTPError, OSError, json.JSONDecodeError, ValueError) as error:
        sys.stderr.write(f"error: Could not list Docker Hub tags: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
