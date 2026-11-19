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


# ---- drop folder (phase 7.8, task 4.4)


def _drop(root, feed, name, data=CSV, mtime=None):
    import os

    folder = root / feed
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_dropzone_takes_the_newest_file_by_modification_time(tmp_path):
    from ports.connectors.dropzone import DropzoneConnector

    _drop(tmp_path, "prices", "z_old.csv", b"a\n1\n", mtime=1_700_000_000)
    _drop(tmp_path, "prices", "a_new.csv", b"a\n2\n", mtime=1_700_000_500)
    fetched = DropzoneConnector(tmp_path).fetch("dropzone/prices")
    assert (fetched.source_ref, fetched.filename, fetched.content) == ("dropzone/prices", "a_new.csv", b"a\n2\n")
    assert DropzoneConnector.name == "dropzone"


def test_dropzone_breaks_a_tie_by_name_last(tmp_path):
    from ports.connectors.dropzone import DropzoneConnector

    _drop(tmp_path, "prices", "a.csv", b"a\n1\n", mtime=1_700_000_000)
    _drop(tmp_path, "prices", "b.csv", b"a\n2\n", mtime=1_700_000_000)
    assert DropzoneConnector(tmp_path).fetch("dropzone/prices").filename == "b.csv"


def test_dropzone_ignores_hidden_part_tmp_files_and_folders(tmp_path):
    from ports.connectors.dropzone import DropzoneConnector

    _drop(tmp_path, "prices", "real.csv", mtime=1_700_000_000)
    _drop(tmp_path, "prices", ".hidden.csv", mtime=1_700_000_900)
    _drop(tmp_path, "prices", "x.csv.part", mtime=1_700_000_900)
    _drop(tmp_path, "prices", "x.tmp", mtime=1_700_000_900)
    (tmp_path / "prices" / "sub").mkdir()
    assert DropzoneConnector(tmp_path).fetch("dropzone/prices").filename == "real.csv"


def test_dropzone_with_no_file_or_no_folder_is_unavailable(tmp_path):
    from ports.connectors.dropzone import DropzoneConnector

    (tmp_path / "empty").mkdir()
    _drop(tmp_path, "only_part", "x.csv.part")
    for ref in ("dropzone/empty", "dropzone/only_part", "dropzone/missing"):
        with pytest.raises(SourceUnavailable, match="no file in the drop folder"):
            DropzoneConnector(tmp_path).fetch(ref)


@pytest.mark.parametrize("ref", ["dropzone/../outside", "dropzone/..", "dropzone/Prices", "dropzone/a/b",
                                 "other/prices", "dropzone/", "dropzone/9x", "dropzone/a-b"])
def test_dropzone_refuses_a_bad_feed_code_and_a_path_out_of_the_root(tmp_path, ref):
    from ports.connectors.dropzone import DropzoneConnector

    (tmp_path.parent / "outside").mkdir(exist_ok=True)
    (tmp_path.parent / "outside" / "x.csv").write_bytes(CSV)
    with pytest.raises(SourceUnavailable):
        DropzoneConnector(tmp_path).fetch(ref)


def test_get_connector_builds_the_dropzone_connector_on_the_configured_root(tmp_path):
    from ports.config import Settings
    from ports.connectors import get_connector

    _drop(tmp_path, "prices", "a.csv")
    with get_connector("dropzone", Settings(_env_file=None, dropzone_root=tmp_path)) as connector:
        assert connector.fetch("dropzone/prices").content == CSV


def test_a_relative_dropzone_root_is_read_from_the_repository():
    from ports.config import REPO_ROOT, Settings

    assert Settings(_env_file=None).dropzone_root == REPO_ROOT / "data" / "dropzone"
    assert Settings(_env_file=None, dropzone_root="data/other").dropzone_root == (REPO_ROOT / "data" / "other").resolve()


def test_write_to_dropzone_is_atomic_named_by_time_and_found_by_the_connector(tmp_path):
    from ports.connectors.dropzone import DropzoneConnector, write_to_dropzone

    first = write_to_dropzone(tmp_path, "prices", "My Prices.csv", b"a\n1\n")
    second = write_to_dropzone(tmp_path, "prices", "My Prices.csv", b"a\n2\n")
    assert first != second and first.endswith("_My_Prices.csv")
    assert sorted(p.name for p in (tmp_path / "prices").iterdir()) == sorted([first, second])  # no .part left
    assert DropzoneConnector(tmp_path).fetch("dropzone/prices").content == b"a\n2\n"


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
    """A connector on a mock transport. data.example.test is a reserved name that never resolves; it is
    allowed here only because the transport is a mock."""
    return HttpConnector(client=httpx.Client(transport=httpx.MockTransport(handler)),
                         allowed_hosts=("data.example.test",))


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


# ---- phase 3: closing, this machine only, a real local server (carried item 2)


def test_http_fetch_from_a_local_server(local_http_server):
    local_http_server.files["/files/volumes.csv"] = CSV
    with HttpConnector() as connector:
        fetched = connector.fetch(f"{local_http_server.url}/files/volumes.csv")
    assert (fetched.content, fetched.filename) == (CSV, "volumes.csv")


def test_http_client_is_closed_after_a_fetch(local_http_server):
    local_http_server.files["/x.csv"] = CSV
    with HttpConnector() as connector:
        connector.fetch(f"{local_http_server.url}/x.csv")
        assert not connector.is_closed
    assert connector.is_closed


@pytest.mark.parametrize("path", ["/missing.csv", None])  # an error status; nothing listening
def test_http_client_is_closed_after_a_failed_fetch(local_http_server, path):
    url = f"{local_http_server.url}{path}" if path else "http://127.0.0.1:9/x.csv"
    with pytest.raises(SourceUnavailable):
        with HttpConnector(timeout_s=0.5) as connector:  # Windows retries a refused connect up to the timeout
            connector.fetch(url)
    assert connector.is_closed


def test_a_client_that_was_given_is_closed_too():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=CSV)))
    with HttpConnector(client=client, allowed_hosts=("data.example.test",)) as connector:
        connector.fetch("https://data.example.test/x.csv")
    assert client.is_closed


@pytest.mark.parametrize("ref", ["https://data.example.test/x.csv", "http://192.0.2.10/x.csv",
                                 "http://127.0.0.1.example.test/x.csv"])
def test_http_refuses_a_host_that_is_not_this_machine(ref):
    """The default allows 127.0.0.1 and localhost only; the refusal comes before any connection."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=CSV)

    with HttpConnector(client=httpx.Client(transport=httpx.MockTransport(handler))) as connector:
        with pytest.raises(SourceUnavailable, match="not allowed"):
            connector.fetch(ref)
    assert calls == []


def test_a_redirect_is_not_followed_and_is_not_a_file():
    """A redirect could lead to a host that is not allowed, so it is never followed; the source is down."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://elsewhere.example.test/x.csv"})

    with pytest.raises(SourceUnavailable, match="redirect"):
        _connector(handler).fetch("https://data.example.test/x.csv")
    assert calls == ["https://data.example.test/x.csv"]


def test_local_file_connector_can_be_closed_like_any_connector(sources):
    with LocalFileConnector(sources) as connector:
        assert connector.fetch("vendor_a/prices.csv").content == CSV
    connector.close()  # closing twice is harmless


def test_the_factory_builds_each_connector_from_the_settings(tmp_path):
    from ports.config import Settings
    from ports.connectors import UnknownConnector, get_connector

    settings = Settings(_env_file=None, source_catalogue=tmp_path / "catalogue.json",
                        source_http_allowed_hosts="127.0.0.1")
    local = get_connector("local_file", settings)
    assert isinstance(local, LocalFileConnector) and local.base_dir == tmp_path.resolve()
    with get_connector("http", settings) as http:
        assert isinstance(http, HttpConnector) and http.allowed_hosts == ("127.0.0.1",)
    with pytest.raises(UnknownConnector, match="ftp"):
        get_connector("ftp", settings)
