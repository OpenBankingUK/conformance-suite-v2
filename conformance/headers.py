"""Immutable case-insensitive HTTP header mappings."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping


class FrozenHeaders(Mapping[str, str]):
    """Immutable HTTP header mapping with case-insensitive lookup.

    Iteration preserves the original casing from the first occurrence of each
    header name. Later values for the same case-insensitive header replace the
    stored value without changing the preserved display casing.
    """

    def __init__(self, headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None) -> None:
        """Copy headers into an immutable case-insensitive mapping.

        Args:
            headers: Source headers mapping or iterable of name/value pairs.
                ``None`` produces an empty mapping.
        """
        header_items = _iter_header_items(headers)
        normalized: dict[str, tuple[str, str]] = {}
        for name, value in header_items:
            lower_name = name.lower()
            if lower_name in normalized:
                original_name, _old_value = normalized[lower_name]
                normalized[lower_name] = (original_name, value)
                continue
            normalized[lower_name] = (name, value)
        self._items = tuple(normalized.values())
        self._lookup = {lower_name: value for lower_name, (_name, value) in normalized.items()}

    def __getitem__(self, key: str) -> str:
        """Return the header value for ``key`` using case-insensitive lookup.

        Args:
            key: Header name to resolve.

        Returns:
            The stored header value.

        Raises:
            KeyError: If the header is not present.
        """
        return self._lookup[key.lower()]

    def __iter__(self) -> Iterator[str]:
        """Iterate header names preserving stored display casing.

        Returns:
            Iterator over header names in insertion order.
        """
        return (name for name, _value in self._items)

    def __len__(self) -> int:
        """Return the number of distinct header names stored.

        Returns:
            Number of case-insensitive header entries.
        """
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` resolves to a stored header name.

        Args:
            key: Candidate header name.

        Returns:
            ``True`` when ``key`` is a string matching a stored header name
            case-insensitively; otherwise ``False``.
        """
        return isinstance(key, str) and key.lower() in self._lookup


def freeze_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None) -> FrozenHeaders:
    """Return an immutable case-insensitive header mapping.

    Args:
        headers: Source headers mapping or iterable of header pairs.

    Returns:
        Frozen copy of the supplied headers.
    """
    return FrozenHeaders(headers)


def _iter_header_items(headers: Mapping[str, str] | Iterable[tuple[str, str]] | None) -> tuple[tuple[str, str], ...]:
    """Normalize supported header inputs to a concrete tuple of pairs.

    Args:
        headers: Source headers mapping or iterable of name/value pairs.

    Returns:
        Tuple of ``(name, value)`` pairs copied from the source.
    """
    if headers is None:
        return ()
    if isinstance(headers, Mapping):
        return tuple(headers.items())
    return tuple(headers)
