from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# AST 节点类型
LITERAL = "literal"
FIELD = "field"
UNARY = "unary"
BINARY = "binary"
FUNC = "func"
CASE = "case"  # IF(cond, a, b) 统一编译成 CASE WHEN


@dataclass
class Node:
    kind: str
    pos: int
    end: int
    # literal
    value: Any = None
    data_type: str | None = None
    # field
    name: str | None = None
    # unary
    op: str | None = None
    operand: "Node | None" = None
    # binary
    left: "Node | None" = None
    right: "Node | None" = None
    # func
    func: str | None = None
    args: list["Node"] = field(default_factory=list)
