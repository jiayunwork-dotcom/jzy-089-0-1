"""Domain specific exceptions shared by the backend modules."""
from __future__ import annotations


class AppError(Exception):
    """Base class for errors that should be surfaced to the API client."""

    status_code = 400

    def __init__(self, message: str, detail: object | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class ValidationError(AppError):
    status_code = 422


class TranslationError(AppError):
    status_code = 400


class ExpressionError(AppError):
    status_code = 422

    def __init__(
        self,
        message: str,
        line: int = 1,
        column: int = 1,
        end_column: int | None = None,
        offset: int = 0,
        length: int = 1,
    ):
        super().__init__(
            message,
            {
                "line": line,
                "column": column,
                "endColumn": end_column or column + length,
                "offset": offset,
                "length": length,
            },
        )
        self.line = line
        self.column = column
        self.end_column = end_column or column + length
        self.offset = offset
        self.length = length


class UnsafeSqlError(AppError):
    status_code = 403
