"""An ONNX model with its tokenizer, loaded from a local folder."""

# onnxruntime has no full type data for InferenceSession, so the "unknown" rules are off in this one
# module (docs/python/index.md). All values that leave the module are typed numpy arrays.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from pathlib import Path

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from tokenizers import Encoding, Tokenizer
from xat_rag_core.errors import ConfigError, StageError

MAX_LENGTH = 512


class OnnxModel:
    """An ONNX Runtime session on the CPU, with a WordPiece tokenizer. Safe for use from several threads."""

    def __init__(self, model_dir: Path, threads: int = 0) -> None:
        """Load `model.onnx` and `tokenizer.json` from `model_dir`. Check the files with `verify_model` first.

        `threads` is the number of threads of ONNX Runtime. 0 lets ONNX Runtime choose.
        """
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            options.use_deterministic_compute = True
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(model_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        except Exception as error:
            raise ConfigError(f"The model in '{model_dir}' could not be loaded.") from error
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        # A second tokenizer that cuts the second text of a pair. Settings are never changed later, so
        # both tokenizers are safe for use from several threads.
        self._truncating = Tokenizer.from_str(self._tokenizer.to_str())
        self._truncating.enable_truncation(MAX_LENGTH, strategy="only_second")
        self._inputs = {item.name for item in self._session.get_inputs()}

    def count(self, text: str) -> int:
        """Return the number of tokens of `text`, with the special tokens."""
        return len(self._tokenizer.encode(text, add_special_tokens=True).ids)

    def encode(self, text: str, pair: str | None = None) -> Encoding:
        """Tokenize a text, or a text pair, with the special tokens. Nothing is cut."""
        return self._tokenizer.encode(text, pair, add_special_tokens=True)

    def encode_truncated(self, text: str, pair: str) -> Encoding:
        """Tokenize a text pair, and cut the second text so that the pair fits the model input."""
        return self._truncating.encode(text, pair, add_special_tokens=True)

    def truncate(self, text: str, max_tokens: int) -> str:
        """Return the longest start of `text` with at most `max_tokens` model tokens, without special tokens."""
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        if len(encoding.ids) <= max_tokens:
            return text
        return text[: encoding.offsets[max_tokens][0]]

    def count_plain(self, text: str) -> int:
        """Return the number of tokens of `text`, without the special tokens."""
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def run(self, encodings: list[Encoding], output: str) -> NDArray[np.float32]:
        """Run the model on a batch of encodings, padded to the longest one, and return one output."""
        if not encodings:
            raise StageError("onnx", "A batch must have at least one text.")
        length = max(len(e.ids) for e in encodings)
        ids = np.zeros((len(encodings), length), dtype=np.int64)
        mask = np.zeros_like(ids)
        types = np.zeros_like(ids)
        for row, encoding in enumerate(encodings):
            size = len(encoding.ids)
            ids[row, :size] = encoding.ids
            mask[row, :size] = encoding.attention_mask
            types[row, :size] = encoding.type_ids
        feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
        feed = {name: value for name, value in feed.items() if name in self._inputs}
        try:
            result = self._session.run([output], feed)
        except Exception as error:
            raise StageError("onnx", "The model failed to run.") from error
        return np.asarray(result[0], dtype=np.float32)
