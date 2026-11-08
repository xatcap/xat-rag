import json
import sqlite3
from pathlib import Path

import pytest
from test_store import manifest
from xat_rag_core.errors import IndexMismatchError, InternalError
from xat_rag_sqlite.manifest import IndexManifest, check_manifest
from xat_rag_sqlite.store import SqliteRagStore


def _old_json(current: IndexManifest) -> str:
    """Return the JSON of a manifest that an older version wrote, with the old key."""
    data = json.loads(current.to_json())
    data["generator_tokenizer_id"] = data.pop("size_tokenizer_id")
    return json.dumps(data, sort_keys=True)


def test_the_manifest_json_uses_the_size_tokenizer_key() -> None:
    data = json.loads(manifest(size_tokenizer_id="size@abc").to_json())
    assert data["size_tokenizer_id"] == "size@abc"
    assert "generator_tokenizer_id" not in data


def test_the_manifest_round_trips_through_json() -> None:
    current = manifest(size_tokenizer_id="size@abc")
    assert IndexManifest.from_json(current.to_json()) == current


def test_the_old_tokenizer_key_is_read_as_the_size_tokenizer() -> None:
    current = manifest(size_tokenizer_id="ministral-3-3b-tokenizer@b35d4dfe")
    read = IndexManifest.from_json(_old_json(current))
    assert read.size_tokenizer_id == "ministral-3-3b-tokenizer@b35d4dfe"
    assert read == current


def test_a_manifest_with_no_tokenizer_key_is_damaged() -> None:
    data = json.loads(manifest().to_json())
    del data["size_tokenizer_id"]
    with pytest.raises(InternalError, match="damaged"):
        IndexManifest.from_json(json.dumps(data))


def test_a_manifest_with_an_unknown_key_is_damaged() -> None:
    data = json.loads(manifest().to_json())
    data["exact_fields"] = ["jira_key"]
    with pytest.raises(InternalError, match="damaged"):
        IndexManifest.from_json(json.dumps(data))


def test_a_different_size_tokenizer_is_a_mismatch() -> None:
    with pytest.raises(IndexMismatchError, match="size_tokenizer_id"):
        check_manifest(manifest(size_tokenizer_id="size@old"), manifest(size_tokenizer_id="size@new"))
    check_manifest(manifest(size_tokenizer_id="size@same"), manifest(size_tokenizer_id="size@same"))


def test_a_store_that_an_older_version_wrote_opens_with_the_same_tokenizer(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    expected = manifest(size_tokenizer_id="size@abc")
    SqliteRagStore(path, expected).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = ? WHERE key = 'manifest'", (_old_json(expected),))
    db.commit()
    db.close()
    store = SqliteRagStore(path, expected)
    assert store.manifest == expected
    store.close()


def test_a_store_that_an_older_version_wrote_is_refused_for_another_tokenizer(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    SqliteRagStore(path, manifest(size_tokenizer_id="size@old")).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = ? WHERE key = 'manifest'", (_old_json(manifest(size_tokenizer_id="size@old")),))
    db.commit()
    db.close()
    with pytest.raises(IndexMismatchError, match="size_tokenizer_id"):
        SqliteRagStore(path, manifest(size_tokenizer_id="size@new"))
