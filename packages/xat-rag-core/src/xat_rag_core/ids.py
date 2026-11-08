"""Content hashes, chunk IDs and hashed IDs for logs."""

import hashlib
import re
import unicodedata

_SLUG_REMOVE = re.compile(r"[\W_]+")


def sha256_hex(data: bytes) -> str:
    """Return the sha256 of `data` as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def chunk_content_hash(header: str, text: str) -> str:
    """Return the content hash of a chunk: the sha256 of the NFC header and text."""
    normalized = unicodedata.normalize("NFC", header) + "\n\x1f\n" + unicodedata.normalize("NFC", text)
    return sha256_hex(normalized.encode("utf-8"))


def log_id(value: str) -> str:
    """Return a hashed ID for logs. Chunk IDs can hold titles and paths, so logs never hold them."""
    return sha256_hex(value.encode("utf-8"))[:12]


def slug(text: str) -> str:
    """Return a stable anchor part: lowercase letters and digits of any script, joined with `-`.

    Accents are removed (`Über` gives `uber`). Other scripts stay (`Установка` gives `установка`), so that
    headings in any language give their own anchors. A slug never holds `~`, `/` or `#`.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SLUG_REMOVE.sub("-", without_marks.casefold()).strip("-") or "x"


def heading_anchor(path: list[str]) -> str:
    """Return the anchor `h/<slug>/<slug>` for a heading path."""
    return "h/" + "/".join(slug(part) for part in path) if path else "h"


def ordinal_anchor(ordinal: int, content_hash: str) -> str:
    """Return the fallback anchor `n/<ordinal>-<hash8>`."""
    return f"n/{ordinal}-{content_hash[:8]}"


def chunk_id(doc_id: str, anchor: str) -> str:
    """Return the chunk ID `<doc_id>#<anchor>`."""
    return f"{doc_id}#{anchor}"


def unique_ids(ids: list[str]) -> list[str]:
    """Make each ID unique by adding `~2`, `~3` and so on to repeated IDs, in order."""
    used: set[str] = set()
    counts: dict[str, int] = {}
    result: list[str] = []
    for value in ids:
        candidate = value
        count = counts.get(value, 1)
        while candidate in used:
            count += 1
            candidate = f"{value}~{count}"
        counts[value] = count
        used.add(candidate)
        result.append(candidate)
    return result
