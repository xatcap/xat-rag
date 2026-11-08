"""`OnnxEmbeddings`: the embedder of xat-rag, as a LangChain `Embeddings`."""

from collections.abc import Sequence
from typing import override

import numpy as np
from langchain_core.embeddings import Embeddings
from numpy.typing import NDArray
from xat_rag_core.errors import StageError

from xat_rag_onnx.session import MAX_LENGTH, OnnxModel

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class OnnxEmbeddings(Embeddings):
    """A BERT-family embedder with CLS pooling and L2 normalization (v1: `bge-small-en-v1.5`).

    xat-rag calls `embed_documents_np` and `embed_query_np`. The list methods exist for LangChain callers.
    """

    def __init__(
        self,
        model: OnnxModel,
        model_id: str,
        dims: int,
        *,
        query_prefix: str = BGE_QUERY_PREFIX,
        document_prefix: str = "",
        batch_size: int = 32,
    ) -> None:
        """Make an embedder from a loaded model."""
        self.model = model
        self.model_id = model_id
        self.dims = dims
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self._batch_size = batch_size

    def count_document_tokens(self, text: str) -> int:
        """Return the number of model tokens of a document text, with its prefix and the special tokens."""
        return self.model.count(self.document_prefix + text)

    def _embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        encodings = [self.model.encode(text) for text in texts]
        if any(len(e.ids) > MAX_LENGTH for e in encodings):
            raise StageError("embed", f"A text is longer than {MAX_LENGTH} model tokens. The chunker must split it.")
        rows: list[NDArray[np.float32]] = []
        for start in range(0, len(encodings), self._batch_size):
            hidden = self.model.run(encodings[start : start + self._batch_size], "last_hidden_state")
            cls = hidden[:, 0, :]
            norms = np.linalg.norm(cls, axis=1, keepdims=True)
            rows.append((cls / np.maximum(norms, 1e-12)).astype(np.float32))
        result = np.concatenate(rows, axis=0)
        if result.shape[1] != self.dims:
            raise StageError("embed", f"The model gives {result.shape[1]} dimensions, not {self.dims}.")
        return result

    def embed_documents_np(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """Embed document texts. Return one normalized row for each text."""
        return self._embed([self.document_prefix + text for text in texts])

    def embed_query_np(self, text: str) -> NDArray[np.float32]:
        """Embed a query, with the query prefix of the model. A long query is cut to the model input.

        Documents are never cut (the chunker splits them). A query is user input, so it is cut, not refused.
        """
        room = MAX_LENGTH - 2 - self.model.count_plain(self.query_prefix)
        return self._embed([self.query_prefix + self.model.truncate(text, room)])[0]

    @override
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(value) for value in row] for row in self.embed_documents_np(texts)]

    @override
    def embed_query(self, text: str) -> list[float]:
        return [float(value) for value in self.embed_query_np(text)]
