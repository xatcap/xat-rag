"""`OnnxCrossEncoderCompressor`: the reranker of xat-rag, as a LangChain `BaseDocumentCompressor`."""

from collections.abc import Sequence
from typing import override

import numpy as np
from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from numpy.typing import NDArray
from pydantic import ConfigDict, Field
from tokenizers import Encoding
from xat_rag_core import lc_codec

from xat_rag_onnx.session import MAX_LENGTH, OnnxModel


class OnnxCrossEncoderCompressor(BaseDocumentCompressor):
    """Scores (query, passage) pairs with a cross-encoder, and keeps the best `top_n`.

    A pair longer than the model input keeps the query and cuts the passage at the end. The
    document then has `rerank_truncated` in its metadata. Ties are broken by chunk ID.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: OnnxModel
    model_id: str
    top_n: int = Field(default=8, ge=1, le=1000)
    batch_size: int = Field(default=32, ge=1, le=512)

    def score(self, query: str, passages: Sequence[str]) -> tuple[NDArray[np.float32], list[bool]]:
        """Return the score of each pair, and whether each passage was cut."""
        if not passages:
            return np.zeros(0, dtype=np.float32), []
        query = self.model.truncate(query, MAX_LENGTH // 2)  # User input is cut to 256 tokens, not refused.
        encodings: list[Encoding] = []
        truncated: list[bool] = []
        for passage in passages:
            encoding = self.model.encode(query, passage)
            cut = len(encoding.ids) > MAX_LENGTH
            encodings.append(self.model.encode_truncated(query, passage) if cut else encoding)
            truncated.append(cut)
        scores = [
            self.model.run(encodings[start : start + self.batch_size], "logits")[:, 0]
            for start in range(0, len(encodings), self.batch_size)
        ]
        return np.concatenate(scores).astype(np.float32), truncated

    @override
    def compress_documents(
        self, documents: Sequence[Document], query: str, callbacks: Callbacks | None = None
    ) -> Sequence[Document]:
        if not documents:
            return []
        scores, truncated = self.score(query, [_rerank_text(d) for d in documents])
        ranked = sorted(
            zip(documents, scores.tolist(), truncated, strict=True),
            key=lambda item: (-item[1], lc_codec.chunk_id_of(item[0])),
        )
        return [lc_codec.with_rerank_score(document, score, cut) for document, score, cut in ranked[: self.top_n]]


def _rerank_text(document: Document) -> str:
    chunk = lc_codec.document_to_chunk(document)
    return f"{chunk.header}\n{chunk.text}"
