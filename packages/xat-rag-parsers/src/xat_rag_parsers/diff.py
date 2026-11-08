"""`DiffParser` (`diff-v1`): one section for each file hunk of a unified diff."""

import posixpath

from unidiff.errors import UnidiffParseError
from unidiff.patch import PatchSet
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.model import LineSpan, ParsedDocument, ParseReport, RawDocument, Scalar, Section
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text


def _change(added: bool, removed: bool, renamed: bool) -> str:
    if added:
        return "added"
    if removed:
        return "removed"
    if renamed:
        return "renamed"
    return "modified"


class DiffParser:
    """A unified diff. The document metadata can give `repo`, `sha` and `message` for the headers."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "diff"

    @property
    def version(self) -> str:
        """The parser version."""
        return "diff-v1"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        # Sanitizing runs after unidiff, on each hunk. Before, it could change line breaks and break the hunk counts.
        text = decode_text(raw.data)
        try:
            patch = PatchSet.from_string(text)
        except UnidiffParseError as error:
            raise ParseError(ParseReason.INVALID, "The document is not a valid unified diff.") from error
        if text.strip() and len(patch) == 0:
            raise ParseError(ParseReason.INVALID, "The document has no file changes. It is not a unified diff.")
        repo = str(raw.metadata.get("repo", ""))
        sha = str(raw.metadata.get("sha", ""))
        message = str(raw.metadata.get("message", "")).strip().split("\n", 1)[0]
        where = f"{repo}@{sha[:8]}" if repo or sha else ""
        sections: list[Section] = []
        notes: list[str] = []
        for patched in patch:
            deadline.check()
            path = patched.path
            if patched.is_binary_file:
                notes.append("binary_file_skipped")
                continue
            metadata: dict[str, Scalar] = {
                "path": path,
                "path_name": posixpath.basename(path).lower(),
                "change": _change(patched.is_added_file, patched.is_removed_file, patched.is_rename),
            }
            if len(patched) == 0:
                notes.append("file_without_hunks_skipped")  # A rename, a mode change or an empty file.
                continue
            for number, hunk in enumerate(patched, start=1):
                # Spans use the line numbers of the new file. A pure deletion has a span of one line.
                start = hunk.target_start
                length = hunk.target_length
                header = f"{where}:{path}" if where else path
                if hunk.section_header:
                    header += f" {hunk.section_header.strip()}"
                if message:
                    header += f"\n{message}"
                sections.append(
                    Section(
                        anchor=f"f/{path}/hunk/{number}",
                        kind="hunk",
                        header=header,
                        text=sanitize_text(str(hunk)).rstrip("\n"),
                        span=LineSpan(max(1, start), max(1, start + max(length, 1) - 1)),
                        metadata=metadata,
                    )
                )
        title = raw.title or message or raw.doc_id
        return ParsedDocument(
            source_document(raw, title), tuple(sections), ParseReport(notes=tuple(sorted(set(notes))))
        )
