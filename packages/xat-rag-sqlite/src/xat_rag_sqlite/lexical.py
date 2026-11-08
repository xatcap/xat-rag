"""Code-aware lexical tokens for FTS5. The rules have a version, because a change needs a rebuild.

A compound such as `com.shop.OrderNotFoundException` gives the whole compound
(`com_shop_ordernotfoundexception`), its parts (`com`, `shop`, `ordernotfoundexception`) and
the camel-case parts (`order`, `not`, `found`, `exception`). JIRA keys and SHAs stay whole.
FTS5 uses `unicode61` with `_` as a token character, so the joined compounds stay one token.
"""

import re
import unicodedata

LEXICAL_TOKENIZER_ID = "code-v1"
FTS5_TOKENIZE = "unicode61 remove_diacritics 2 tokenchars '_'"

_COMPOUND = re.compile(r"[^\W_]+(?:[._\-/:$#@][^\W_]+)*")
_SEPARATORS = re.compile(r"[._\-/:$#@]+")
_MAX_TOKEN_LENGTH = 128


def _camel_parts(piece: str) -> list[str]:
    """Split a mixed-case name at case and digit changes, for any script: `HTTPServer2Go` gives HTTP, Server, 2, Go."""
    parts: list[str] = []
    current = piece[0]
    for index in range(1, len(piece)):
        previous, char = piece[index - 1], piece[index]
        following = piece[index + 1] if index + 1 < len(piece) else ""
        boundary = (
            (previous.islower() and char.isupper())
            or (previous.isdigit() != char.isdigit())
            or (previous.isupper() and char.isupper() and following.islower())
        )
        if boundary:
            parts.append(current)
            current = char
        else:
            current += char
    parts.append(current)
    return parts


def _parts(compound: str) -> list[str]:
    tokens: list[str] = []
    pieces = [piece for piece in _SEPARATORS.split(compound) if piece]
    if len(pieces) > 1:
        tokens.append("_".join(piece.lower() for piece in pieces))
    for piece in pieces:
        tokens.append(piece.lower())
        if piece.islower() or piece.isupper() or piece.isdigit():
            continue  # Split only mixed-case names, so that SHAs and keys stay whole.
        camel = _camel_parts(piece)
        if len(camel) > 1:
            tokens.extend(part.lower() for part in camel)
    return tokens


def lexical_tokens(text: str) -> list[str]:
    """Return the lexical tokens of `text`, in order, with repeats."""
    tokens: list[str] = []
    for match in _COMPOUND.finditer(unicodedata.normalize("NFC", text)):
        tokens.extend(token for token in _parts(match.group(0)) if len(token) <= _MAX_TOKEN_LENGTH)
    return tokens


def index_text(text: str) -> str:
    """Return the text that goes into the FTS5 index: the tokens, joined with spaces."""
    return " ".join(lexical_tokens(text))


def match_expression(terms: list[str] | tuple[str, ...], max_tokens: int = 64) -> str | None:
    """Return a safe FTS5 `MATCH` expression for the query terms, or `None` if there is no token.

    Each token is in double quotes, and the tokens are joined with `OR`. Raw text never goes
    into `MATCH`, because characters such as `-` and `:` are FTS5 operators.
    """
    seen: dict[str, None] = {}
    for token in (token for term in terms for token in lexical_tokens(term)):
        seen.setdefault(token)
        if len(seen) >= max_tokens:
            break
    if not seen:
        return None
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in seen)
