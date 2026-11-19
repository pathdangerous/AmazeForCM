"""Source connectors. No env selector and no Azure stub: the connector is chosen per source.

get_connector() builds a new connector for one fetch; the caller closes it (a `with` block).
- local_file reads under the catalogue's folder (SOURCE_CATALOGUE's parent);
- http reaches only SOURCE_HTTP_ALLOWED_HOSTS (this machine until the Azure move);
- dropzone reads the newest file of an uploaded feed's drop folder under DROPZONE_ROOT.
"""

from __future__ import annotations

from ports.config import Settings, get_settings
from ports.connectors.base import FetchedFile, SourceConnector, SourceUnavailable

__all__ = ["FetchedFile", "SourceConnector", "SourceUnavailable", "UnknownConnector", "get_connector"]


class UnknownConnector(ValueError):
    """No connector has this name."""


def get_connector(name: str, settings: Settings | None = None) -> SourceConnector:
    settings = settings or get_settings()
    if name == "local_file":
        from ports.connectors.local_file import LocalFileConnector

        return LocalFileConnector(settings.source_catalogue.parent)
    if name == "http":
        from ports.connectors.http import HttpConnector

        return HttpConnector(allowed_hosts=settings.allowed_source_hosts())
    if name == "dropzone":
        from ports.connectors.dropzone import DropzoneConnector

        return DropzoneConnector(settings.dropzone_root)
    raise UnknownConnector(f"No connector {name!r}; use local_file, http or dropzone")
