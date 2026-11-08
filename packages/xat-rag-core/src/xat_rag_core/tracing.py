"""Run xat-rag code with LangChain and LangSmith tracing off, also in library mode. See ADR 0011.

`untraced` turns off langsmith tracing and clears the tracer context variables of `langchain-core`
for one call. Context variables do not pass into new threads, so call `untraced` inside the worker
thread that does the work. The names of the context variables are internal to `langchain-core`.
A test checks them at each upgrade.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, cast

import langchain_core.tracers.context as tracer_context
from langsmith.run_helpers import tracing_context  # pyright: ignore[reportUnknownVariableType]

from xat_rag_core.errors import InternalError

_TRACER_VARIABLES = ("tracing_v2_callback_var", "run_collector_var")


def tracer_variables() -> list[ContextVar[Any]]:
    """Return the tracer context variables of `langchain-core`. Raise `InternalError` if one is missing."""
    variables: list[ContextVar[Any]] = []
    for name in _TRACER_VARIABLES:
        value: object = getattr(tracer_context, name, None)
        if not isinstance(value, ContextVar):
            raise InternalError(f"langchain-core has no tracer context variable '{name}'. Check the upgrade.")
        variables.append(cast(ContextVar[Any], value))
    return variables


@contextmanager
def no_tracing() -> Generator[None]:
    """Turn off tracing for the code inside the `with` block, in this thread."""
    variables = tracer_variables()
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = [(variable, variable.set(None)) for variable in variables]
    try:
        with tracing_context(enabled=False, parent=False):
            yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def untraced[**P, R](function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Call `function` with tracing off."""
    with no_tracing():
        return function(*args, **kwargs)
