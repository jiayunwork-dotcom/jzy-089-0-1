from __future__ import annotations


class ExpressionError(Exception):
    """表达式校验/编译错误，``start``/``end`` 为字符偏移（左闭右开）。"""

    def __init__(self, message: str, start: int = 0, end: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = len(message) if end is None else end

    def to_dict(self) -> dict:
        return {"message": self.message, "start": self.start, "end": self.end}
