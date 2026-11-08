from xat_rag_core.ids import chunk_content_hash, chunk_id, heading_anchor, log_id, ordinal_anchor, slug, unique_ids


def test_slug_and_heading_anchor_are_stable() -> None:
    assert slug("Deploy on Linux (systemd)") == "deploy-on-linux-systemd"
    assert slug("Über ✓") == "uber"
    assert slug("!!!") == "x"
    assert heading_anchor(["Install", "Linux"]) == "h/install/linux"
    assert chunk_id("docs:a.md", heading_anchor(["A"])) == "docs:a.md#h/a"


def test_content_hash_uses_nfc() -> None:
    assert chunk_content_hash("h", "é") == chunk_content_hash("h", "é")
    assert chunk_content_hash("h", "a") != chunk_content_hash("ha", "")


def test_ordinal_anchor_and_log_id() -> None:
    assert ordinal_anchor(3, "abcdef0123") == "n/3-abcdef01"
    assert len(log_id("jira:PROJ-1#desc")) == 12
    assert log_id("x") == log_id("x")


def test_unique_ids() -> None:
    assert unique_ids(["a", "b", "a", "a"]) == ["a", "b", "a~2", "a~3"]


def test_unique_ids_never_collide() -> None:
    assert unique_ids(["a", "a", "a~2"]) == ["a", "a~2", "a~2~2"]
