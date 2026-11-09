"""The error types of xat-rag. Each kind of failure has one error type and one HTTP status.

Error messages never hold document text or input values.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar


class ErrorType(StrEnum):
    """The `type` field of an error body."""

    INVALID_REQUEST = "invalid_request_error"
    NOT_FOUND = "not_found_error"
    INDEX_MISMATCH = "index_mismatch_error"
    SERVICE_UNAVAILABLE = "service_unavailable_error"
    OVERLOADED = "overloaded_error"
    STAGE_TIMEOUT = "stage_timeout_error"
    PARSE = "parse_error"
    INTERNAL = "internal_error"


HTTP_STATUS: Mapping[ErrorType, int] = MappingProxyType(
    {
        ErrorType.INVALID_REQUEST: 400,
        ErrorType.NOT_FOUND: 404,
        ErrorType.INDEX_MISMATCH: 409,
        ErrorType.SERVICE_UNAVAILABLE: 503,
        ErrorType.OVERLOADED: 503,
        ErrorType.STAGE_TIMEOUT: 504,
        ErrorType.PARSE: 422,
        ErrorType.INTERNAL: 500,
    }
)
"""The HTTP status of each error type. A `parse_error` normally goes into the result of one document."""


class ParseReason(StrEnum):
    """The reason of a `parse_error`."""

    INVALID = "invalid"
    ENCRYPTED = "encrypted"
    NO_TEXT_LAYER = "no_text_layer"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    TOO_LARGE = "too_large"
    LIMIT_EXCEEDED = "limit_exceeded"
    PARSE_TIMEOUT = "parse_timeout"


class XatRagError(Exception):
    """The base class of all xat-rag errors."""

    error_type: ClassVar[ErrorType] = ErrorType.INTERNAL

    def __init__(self, message: str) -> None:
        """Make an error with a message that a person can act on."""
        super().__init__(message)
        self.message = message

    @property
    def status(self) -> int:
        """The HTTP status of this error."""
        return HTTP_STATUS[self.error_type]


class InvalidRequestError(XatRagError):
    """The request or a recipe is not valid."""

    error_type = ErrorType.INVALID_REQUEST


class ConfigError(InvalidRequestError):
    """A setting or a recipe is not valid. At startup, the process stops with this message."""


class NotFoundError(XatRagError):
    """An unknown RAG, document, job."""

    error_type = ErrorType.NOT_FOUND


class IndexMismatchError(XatRagError):
    """The index manifest does not match the loaded models, chunkers or recipe."""

    error_type = ErrorType.INDEX_MISMATCH


class ServiceUnavailableError(XatRagError):
    """The service cannot take the request now."""

    error_type = ErrorType.SERVICE_UNAVAILABLE

    def __init__(self, message: str, retry_after_seconds: int | None) -> None:
        """Make the error. `retry_after_seconds` is `None` when a retry does not help."""
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class OverloadedError(XatRagError):
    """The request queue is full."""

    error_type = ErrorType.OVERLOADED

    def __init__(self, message: str, retry_after_seconds: int = 1) -> None:
        """Make the error."""
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class StageTimeoutError(XatRagError):
    """A stage took longer than its time limit."""

    error_type = ErrorType.STAGE_TIMEOUT

    def __init__(self, stage: str, limit_ms: int) -> None:
        """Make the error. The message names the stage and its limit."""
        super().__init__(f"The stage '{stage}' took longer than its limit of {limit_ms} ms.")
        self.stage = stage
        self.limit_ms = limit_ms


class ParseError(XatRagError):
    """One document did not parse."""

    error_type = ErrorType.PARSE

    def __init__(self, reason: ParseReason, message: str) -> None:
        """Make the error with a reason and a message."""
        super().__init__(message)
        self.reason = reason


class StageError(XatRagError):
    """A pipeline stage failed, for example the reranker. There is no silent fallback."""

    error_type = ErrorType.INTERNAL

    def __init__(self, stage: str, message: str) -> None:
        """Make the error. The message names the stage."""
        super().__init__(f"The stage '{stage}' failed: {message}")
        self.stage = stage


class InternalError(XatRagError):
    """An unexpected failure. The HTTP response has no details."""

    error_type = ErrorType.INTERNAL
