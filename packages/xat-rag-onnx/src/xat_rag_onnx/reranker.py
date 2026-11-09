"""`OnnxReranker`: the cross-encoder reranker of xat-rag."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from tokenizers import Encoding
from xat_rag_core.model import Candidate

from xat_rag_onnx.session import MAX_LENGTH, OnnxModel


@dataclass(frozen=True, slots=True)
class OnnxReranker:
    """Scores (query, passage) pairs with a cross-encoder, and keeps the best `top_n`.

    A pair longer than the model input keeps the query and cuts the passage at the end. The
    candidate then has `rerank_truncated` in its scores. Ties are broken by chunk ID.
    """

    model: OnnxModel
    model_id: str
    top_n: int = 8
    batch_size: int = 32

    def __post_init__(self) -> None:
        """Check the settings."""
        if not 1 <= self.top_n <= 1000 or not 1 <= self.batch_size <= 512:
            raise ValueError("top_n must be from 1 to 1000, and batch_size from 1 to 512.")

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

    def rerank(self, candidates: Sequence[Candidate], query: str) -> list[Candidate]:
        """Return the best candidates with their rerank scores, best first."""
        if not candidates:
            return []
        scores, truncated = self.score(query, [f"{c.chunk.header}\n{c.chunk.text}" for c in candidates])
        ranked = sorted(
            zip(candidates, scores.tolist(), truncated, strict=True), key=lambda item: (-item[1], item[0].chunk_id)
        )
        return [
            replace(candidate, scores=replace(candidate.scores, rerank_score=score, rerank_truncated=cut))
            for candidate, score, cut in ranked[: self.top_n]
        ]
