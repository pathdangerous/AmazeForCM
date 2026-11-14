"""HTTP connector: GET an http(s) URL. Any transport error or status of 400 or more is SourceUnavailable.

Phase 3 (carried item 2): the connector closes its client, including one it was given. Use it in a
`with` block, or call close(). Only allowed hosts are reached: by default this machine (127.0.0.1 and
localhost), until the Azure move. Another host is refused before any connection is opened, and a
redirect is never followed (it could lead elsewhere): it counts as the source being down.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

import httpx

from ports.connectors.base import FetchedFile, SourceUnavailable

THIS_MACHINE = ("127.0.0.1", "localhost")


class HttpConnector:
    name = "http"

    def __init__(self, client: httpx.Client | None = None, timeout_s: float = 30.0,
                 allowed_hosts: Iterable[str] = THIS_MACHINE):
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)

    def __enter__(self) -> HttpConnector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    def fetch(self, source_ref: str) -> FetchedFile:
        parts = urlsplit(source_ref)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SourceUnavailable(f"Not an http(s) URL: {source_ref!r}")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise SourceUnavailable(f"Host {host!r} is not allowed (allowed: {', '.join(self.allowed_hosts)})")
        try:
            response = self._client.get(source_ref)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"Cannot reach {source_ref}: {exc}") from exc
        if response.is_redirect:
            # Never followed: the new location could be a host that is not allowed.
            raise SourceUnavailable(f"{source_ref} answered with a redirect (HTTP {response.status_code}); "
                                    "redirects are not followed")
        if response.status_code >= 400:
            raise SourceUnavailable(f"{source_ref} returned HTTP {response.status_code}")
        filename = parts.path.rsplit("/", 1)[-1] or "download"
        return FetchedFile(
            source_ref=source_ref,
            filename=filename,
            content=response.content,
            content_type=response.headers.get("content-type"),
        )
