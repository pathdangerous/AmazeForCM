"""Source connectors: local file and HTTP. A source that cannot be read raises SourceUnavailable."""

import httpx
import pytest

from ports.connectors.base import FetchedFile, SourceUnavailable
from ports.connectors.http import HttpConnector
from ports.connectors.local_file import LocalFileConnector

CSV = b"instrument_id,close\nSYN-001,101.25\n"


# ---- local file


@pytest.fixture
def sources(tmp_path):
    (tmp_path / "vendor_a").mkdir()
    (tmp_path / "vendor_a" / "prices.csv").write_bytes(CSV)
    return tmp_path


def test_local_fetch(sources):
    fetched = LocalFileConnector(sources).fetch("vendor_a/prices.csv")
    assert isinstance(fetched, FetchedFile)
    assert (fetched.source_ref, fetched.filename, fetched.content) == ("vendor_a/prices.csv", "prices.csv", CSV)
    assert fetched.size == len(CSV)
    assert fetched.content_type == "text/csv"


def test_local_missing_file(sources):
    with pytest.raises(SourceUnavailable, match="nope.csv"):
        LocalFileConnector(sources).fetch("vendor_a/nope.csv")


@pytest.mark.parametrize("ref", ["../outside.csv", "vendor_a/../../outside.csv", "C:/Windows/win.ini", "vendor_a"])
def test_local_ref_cannot_leave_the_sources_folder_or_be_a_folder(sources, ref):
    with pytest.raises(SourceUnavailable):
        LocalFileConnector(sources).fetch(ref)


# ---- HTTP


def _connector(handler) -> HttpConnector:
    return HttpConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_http_fetch():
    def handler(request):
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv; charset=utf-8"})

    fetched = _connector(handler).fetch("https://data.example.test/v1/prices.csv?day=2026-01-01")
    assert fetched.content == CSV
    assert fetched.filename == "prices.csv"
    assert fetched.content_type == "text/csv; charset=utf-8"


@pytest.mark.parametrize("status", [404, 500, 503])
def test_http_error_status_is_source_unavailable(status):
    with pytest.raises(SourceUnavailable, match=str(status)):
        _connector(lambda r: httpx.Response(status)).fetch("https://data.example.test/prices.csv")


def test_http_connection_error_is_source_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SourceUnavailable, match="refused"):
        _connector(handler).fetch("https://data.example.test/prices.csv")


@pytest.mark.parametrize("ref", ["ftp://data.example.test/x.csv", "file:///C:/x.csv", "not a url"])
def test_http_only_accepts_http_and_https(ref):
    with pytest.raises(SourceUnavailable):
        _connector(lambda r: httpx.Response(200)).fetch(ref)
