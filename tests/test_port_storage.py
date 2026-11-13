"""Storage port, local adapter: blobs are files under LOCAL_BLOB_ROOT."""

import pytest

from ports.config import REPO_ROOT, Settings
from ports.storage import get_blob_store
from ports.storage.base import BlobNotFound, InvalidBlobKey
from ports.storage.local import LocalBlobStore


@pytest.fixture
def store(tmp_path):
    return LocalBlobStore(tmp_path / "blob")


def test_round_trip(store):
    ref = store.put("raw/feed-a/2026-01-01.csv", b"id,price\n1,10.5\n")
    assert ref == "local://raw/feed-a/2026-01-01.csv"
    assert store.get("raw/feed-a/2026-01-01.csv") == b"id,price\n1,10.5\n"
    assert store.exists("raw/feed-a/2026-01-01.csv")


def test_put_overwrites(store):
    store.put("a.txt", b"one")
    store.put("a.txt", b"two")
    assert store.get("a.txt") == b"two"


def test_missing_blob(store):
    assert not store.exists("nope.txt")
    with pytest.raises(BlobNotFound, match="nope.txt"):
        store.get("nope.txt")


def test_list_keys_with_prefix(store):
    for key in ("raw/b.csv", "raw/a.csv", "curated/c.csv"):
        store.put(key, b"x")
    assert store.list_keys() == ["curated/c.csv", "raw/a.csv", "raw/b.csv"]
    assert store.list_keys("raw/") == ["raw/a.csv", "raw/b.csv"]
    assert store.list_keys("none/") == []


@pytest.mark.parametrize(
    "key", ["", "../escape.txt", "a/../../escape.txt", "/abs.txt", "C:/abs.txt", "a\\b.txt", "a//b.txt", "a/./b", "dir/"]
)
def test_key_cannot_escape_root_or_be_malformed(store, key):
    with pytest.raises(InvalidBlobKey):
        store.put(key, b"x")
    with pytest.raises(InvalidBlobKey):
        store.get(key)


def test_nothing_written_outside_root(store, tmp_path):
    with pytest.raises(InvalidBlobKey):
        store.put("../escape.txt", b"x")
    assert not (tmp_path / "escape.txt").exists()


def test_factory_resolves_relative_root_against_repo():
    store = get_blob_store(Settings(_env_file=None, local_blob_root="./data/blob"))
    assert store.root == (REPO_ROOT / "data" / "blob").resolve()


# ---- added in phase 1 (task 1): each closes a gap found by a deliberate break, or a real bug


@pytest.mark.parametrize("key", ["a/../b.txt", "raw/../raw/a.csv"])
def test_dotdot_is_rejected_even_when_it_stays_inside_root(store, key):
    """Break 6: without the '..' check, the escape guard alone let these through."""
    with pytest.raises(InvalidBlobKey):
        store.put(key, b"x")
    with pytest.raises(InvalidBlobKey):
        store.get(key)


def test_factory_root_does_not_depend_on_current_folder(tmp_path, monkeypatch):
    """Break 9: tests run from the repository root, so a cwd-relative root went unnoticed."""
    monkeypatch.chdir(tmp_path)
    store = get_blob_store(Settings(_env_file=None, local_blob_root="./data/blob"))
    assert store.root == (REPO_ROOT / "data" / "blob").resolve()


def test_list_keys_ignores_unfinished_writes(store):
    """Break 8: a temp file left by a crashed write must never show up as a blob."""
    target = store.root / "raw" / "a.csv"
    target.parent.mkdir(parents=True)
    store._temp_path(target).write_bytes(b"half written")
    assert store.list_keys() == []
    assert not store.exists("raw/a.csv")


def test_failed_put_leaves_nothing_behind(store, monkeypatch):
    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("ports.storage.local.os.replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.put("raw/a.csv", b"x")
    assert [p for p in store.root.rglob("*") if p.is_file()] == []


def test_put_does_not_touch_a_neighbouring_tmp_key(store):
    """Real bug in phase 0: put("report") wrote report.tmp first, destroying the blob "report.tmp"."""
    store.put("report.tmp", b"keep me")
    store.put("report", b"new")
    assert store.get("report.tmp") == b"keep me"
    assert store.get("report") == b"new"


def test_key_ending_in_tmp_is_listed(store):
    """Real bug in phase 0: list_keys hid every key ending in .tmp."""
    store.put("notes.tmp", b"x")
    assert store.list_keys() == ["notes.tmp"]


@pytest.mark.parametrize("key", [".hidden", "raw/.a.csv", ".dir/a.csv"])
def test_segments_starting_with_a_dot_are_rejected(store, key):
    """Dot-names are reserved for unfinished writes, so a key cannot use them."""
    with pytest.raises(InvalidBlobKey):
        store.put(key, b"x")
