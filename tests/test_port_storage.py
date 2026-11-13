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
