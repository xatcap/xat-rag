"""`JiraParser` (`jira-v1`): a JIRA REST issue as JSON. Summary and description, then one section for each comment.

Author and assignee names never go into the text. Mentions become `@user`. See ADR 0010.
"""

import json
import re
from collections.abc import Mapping
from typing import Any, cast

from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Scalar, Section
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text

_BLOCKS = frozenset(
    {"paragraph", "heading", "listItem", "codeBlock", "blockquote", "tableRow", "tableCell", "tableHeader", "rule"}
)
# JIRA wiki markup (API v2) mentions: [~jdoe] and [~accountid:5b10a2844c20165700ede21g].
_WIKI_MENTION = re.compile(r"\[~[^\]\n]{1,200}\]")
_COMMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_DEPTH = 64


def adf_to_text(node: object, depth: int = 0) -> str:
    """Return the plain text of an Atlassian Document Format node, or the text itself for a string."""
    if isinstance(node, str):
        return _WIKI_MENTION.sub("@user", node)
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return ""
    data = cast(Mapping[str, Any], node)
    kind = data.get("type")
    if kind == "text":
        return str(data.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return "@user"
    children = cast(list[object], data.get("content", []) if isinstance(data.get("content"), list) else [])
    text = "".join(adf_to_text(child, depth + 1) for child in children)
    return text + "\n" if kind in _BLOCKS else text


def _name(value: object) -> str:
    if isinstance(value, dict):
        return str(cast(Mapping[str, Any], value).get("name", ""))
    return ""


def _names(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ",".join(sorted(str(item) if isinstance(item, str) else _name(item) for item in cast(list[object], value)))


def _section(
    anchor: str, kind: str, header: str, text: str, *, metadata: Mapping[str, Scalar], parent: str | None
) -> Section:
    text = sanitize_text(text).strip("\n")
    return Section(anchor, kind, header, text, LineSpan(1, max(1, text.count("\n") + 1)), parent, metadata=metadata)


class JiraParser:
    """A JIRA issue in the REST JSON format (API v2 text or API v3 ADF)."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "jira"

    @property
    def version(self) -> str:
        """The parser version."""
        return "jira-v1"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        try:
            issue = json.loads(decode_text(raw.data))
        except (ValueError, RecursionError) as error:
            raise ParseError(ParseReason.INVALID, "The document is not valid JSON.") from error
        if not isinstance(issue, dict) or not isinstance(cast(dict[str, Any], issue).get("fields"), dict):
            raise ParseError(ParseReason.INVALID, "The document is not a JIRA issue: it has no 'fields' object.")
        data = cast(dict[str, Any], issue)
        fields = cast(dict[str, Any], data["fields"])
        key = str(data.get("key", ""))
        summary = " ".join(str(fields.get("summary") or "").split())
        metadata: dict[str, Scalar] = {
            "jira_key": key,
            "status": _name(fields.get("status")),
            "resolution": _name(fields.get("resolution")),
            "issue_type": _name(fields.get("issuetype")),
            "priority": _name(fields.get("priority")),
            "components": _names(fields.get("components")),
            "labels": _names(fields.get("labels")),
            "created": str(fields.get("created", "")),
            "updated": str(fields.get("updated", "")),
        }
        title = f"{key}: {summary}" if key else summary
        description = adf_to_text(fields.get("description") or "")
        sections = [
            _section("desc", "description", title, f"{summary}\n\n{description}", metadata=metadata, parent=None)
        ]
        comment_block = fields.get("comment")
        comments: list[object] = []
        if isinstance(comment_block, dict):
            listed = cast(dict[str, Any], comment_block).get("comments")
            if isinstance(listed, list):
                comments = cast(list[object], listed)
        for position, comment in enumerate(comments, start=1):
            deadline.check()
            if not isinstance(comment, dict):
                continue
            entry = cast(dict[str, Any], comment)
            raw_id = str(entry.get("id") or "")
            comment_id = raw_id if _COMMENT_ID.fullmatch(raw_id) else str(position)
            body = adf_to_text(entry.get("body") or "")
            created = str(entry.get("created", ""))
            sections.append(
                _section(
                    f"comment/{comment_id}",
                    "comment",
                    f"{title} > comment {comment_id}",
                    body,
                    metadata={**metadata, "comment_created": created},
                    parent="desc",
                )
            )
        return ParsedDocument(source_document(raw, title), tuple(sections))
