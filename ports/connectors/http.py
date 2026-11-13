"""HTTP connector: GET an http(s) URL. Any transport error or status of 400 or more is SourceUnavailable."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from ports.connectors.base import FetchedFile, SourceUnavailable


class HttpConnector:
    name = "http"

    def __init__(self, client: httpx.Client | None = None, timeout_s: float = 30.0):
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=True)

    def fetch(self, source_ref: str) -> FetchedFile:
        parts = urlsplit(source_ref)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SourceUnavailable(f"Not an http(s) URL: {source_ref!r}")
        try:
            response = self._client.get(source_ref)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Cannot reach {source_ref}: {exc}") from exc
        if response.status_code >= 400:
            raise SourceUnavailable(f"{source_ref} returned HTTP {response.status_code}")
        filename = parts.path.rsplit("/", 1)[-1] or "download"
        return FetchedFile(
            source_ref=source_ref,
            filename=filename,
            content=response.content,
            content_type=response.headers.get("content-type"),
        )
