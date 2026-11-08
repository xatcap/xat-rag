from typing import Any, override

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.schemas import Run
from xat_rag_core.tracing import no_tracing, tracer_variables, untraced


class _Retriever(BaseRetriever):
    @override
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return [Document(page_content=query)]


class _Recorder(BaseTracer):
    """A local tracer, like the one a host app could set. It sends nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[Run] = []

    @override
    def _persist_run(self, run: Run) -> None:
        self.runs.append(run)


def test_tracer_variables_exist() -> None:
    assert len(tracer_variables()) == 2


def test_a_host_tracer_gets_no_runs_from_untraced_calls() -> None:
    variable = tracer_variables()[0]
    recorder = _Recorder()
    token = variable.set(recorder)
    try:
        _Retriever().invoke("visible to the host")
        assert len(recorder.runs) == 1
        with no_tracing():
            _Retriever().invoke("secret chunk text", config={"callbacks": []})
        assert len(recorder.runs) == 1
        assert variable.get() is recorder
    finally:
        variable.reset(token)


def test_untraced_returns_the_value() -> None:
    def add(value: int, extra: Any = 1) -> int:
        return value + int(extra)

    assert untraced(add, 1) == 2
