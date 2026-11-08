from xat_rag_core.ids import chunk_content_hash
from xat_rag_core.model import Chunk, LineSpan, SourceKind


def make_chunk(chunk_id: str, text: str = "text", rag: str = "docs", kind: SourceKind = SourceKind.MARKDOWN) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#", 1)[0],
        rag=rag,
        source_kind=kind,
        kind="section",
        header=f"{chunk_id} header",
        text=text,
        span=LineSpan(1, 3),
        content_hash=chunk_content_hash(f"{chunk_id} header", text),
        chunker_version="markdown-v1",
    )
