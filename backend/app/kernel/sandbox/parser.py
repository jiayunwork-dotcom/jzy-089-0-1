"""递归下降解析器（白名单沙箱）。

文法：
    expr     := or_expr
    or       := and (OR and)*
    and      := not (AND not)*
    not      := NOT not | cmp
    cmp      := add ( (=|<>|!=|<|<=|>|>=) add )?
    add      := mul ((+|-) mul)*
    mul      := unary (( * | / | % ) unary)*
    unary    := (-|+) unary | primary
    primary  := NUMBER | STRING | TRUE|FALSE|NULL | IDENT | IDENT ( args ) | ( expr )

解析阶段只做语法；函数白名单、类型检查在 validate() 中做（见 compiler.py）。
"""
from __future__ import annotations

from . import ast_nodes as ast
from .errors import ExpressionError

KEYWORDS = {"AND", "OR", "NOT", "TRUE", "FALSE", "NULL", "IS"}


class Token:
    __slots__ = ("kind", "value", "pos", "end")

    def __init__(self, kind: str, value, pos: int, end: int) -> None:
        self.kind = kind
        self.value = value
        self.pos = pos
        self.end = end

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.kind}, {self.value!r})"


class Lexer:
    def __init__(self, src: str) -> None:
        self.src = src
        self.i = 0
        self.n = len(src)

    def error(self, msg: str, pos: int, end: int | None = None) -> ExpressionError:
        return ExpressionError(msg, pos, self.n if end is None else end)

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        s = self.src
        while self.i < self.n:
            ch = s[self.i]
            if ch in " \t\r\n":
                self.i += 1
                continue
            start = self.i
            if ch.isdigit() or (ch == "." and self.i + 1 < self.n and s[self.i + 1].isdigit()):
                tokens.append(self._number())
                continue
            if ch == "'" or ch == '"':
                tokens.append(self._string(ch))
                continue
            if ch.isalpha() or ch == "_":
                word = self._ident()
                upper = word.value.upper()
                if upper in KEYWORDS:
                    tokens.append(Token("kw", upper, word.pos, word.end))
                else:
                    tokens.append(word)
                continue
            two = s[self.i:self.i + 2]
            if two in ("<>", "!=", "<=", ">="):
                tokens.append(Token("op", two, start, start + 2))
                self.i += 2
                continue
            if ch in "+-*/%(),=<>":
                tokens.append(Token("op", ch, start, start + 1))
                self.i += 1
                continue
            raise self.error(f"无法识别的字符 {ch!r}", start, start + 1)
        tokens.append(Token("eof", None, self.n, self.n))
        return tokens

    def _number(self) -> Token:
        s = self.src
        start = self.i
        dot = False
        while self.i < self.n and (s[self.i].isdigit() or s[self.i] == "."):
            if s[self.i] == ".":
                if dot:
                    raise self.error("数字中出现了多个小数点", start, self.i + 1)
                dot = True
            self.i += 1
        text = s[start:self.i]
        value: float | int = float(text) if dot else int(text)
        return Token("num", value, start, self.i)

    def _string(self, quote: str) -> Token:
        s = self.src
        start = self.i
        self.i += 1
        buf: list[str] = []
        while self.i < self.n:
            ch = s[self.i]
            if ch == quote:
                # SQL 风格的双引号转义：'' / ""
                if self.i + 1 < self.n and s[self.i + 1] == quote:
                    buf.append(quote)
                    self.i += 2
                    continue
                self.i += 1
                return Token("str", "".join(buf), start, self.i)
            buf.append(ch)
            self.i += 1
        raise self.error("字符串没有结束引号", start, self.n)

    def _ident(self) -> Token:
        s = self.src
        start = self.i
        self.i += 1
        while self.i < self.n and (s[self.i].isalnum() or s[self.i] == "_"):
            self.i += 1
        return Token("ident", s[start:self.i], start, self.i)


class Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.tokens = Lexer(src).tokenize()
        self.p = 0

    # -- 基础工具 ------------------------------------------------------------
    @property
    def cur(self) -> Token:
        return self.tokens[self.p]

    def advance(self) -> Token:
        t = self.tokens[self.p]
        self.p += 1
        return t

    def accept(self, kind: str, value=None) -> Token | None:
        t = self.cur
        if t.kind == kind and (value is None or t.value == value):
            return self.advance()
        return None

    def expect(self, kind: str, value=None) -> Token:
        t = self.cur
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            if t.kind == "eof":
                raise ExpressionError(f"表达式不完整，期望 {want}", t.pos, t.pos + 1)
            raise ExpressionError(f"期望 {want}，但出现了 {t.value!r}", t.pos, t.end)
        return self.advance()

    # -- 文法规则 ------------------------------------------------------------
    def parse(self) -> ast.Node:
        node = self._or()
        if self.cur.kind != "eof":
            t = self.cur
            raise ExpressionError(f"多余的内容 {t.value!r}", t.pos, t.end)
        return node

    def _or(self) -> ast.Node:
        node = self._and()
        while self.accept("kw", "OR"):
            op_tok = self.tokens[self.p - 1]
            right = self._and()
            node = ast.Node(ast.BINARY, node.pos, right.end, op="OR", left=node, right=right)
        return node

    def _and(self) -> ast.Node:
        node = self._not()
        while self.accept("kw", "AND"):
            op_tok = self.tokens[self.p - 1]
            right = self._not()
            node = ast.Node(ast.BINARY, node.pos, right.end, op="AND", left=node, right=right)
        return node

    def _not(self) -> ast.Node:
        if tok := self.accept("kw", "NOT"):
            operand = self._not()
            return ast.Node(ast.UNARY, tok.pos, operand.end, op="NOT", operand=operand)
        return self._cmp()

    def _cmp(self) -> ast.Node:
        node = self._add()
        t = self.cur
        if t.kind == "op" and t.value in ("=", "<>", "!=", "<", "<=", ">", ">="):
            self.advance()
            right = self._add()
            op = "<>" if t.value == "!=" else t.value
            node = ast.Node(ast.BINARY, node.pos, right.end, op=op, left=node, right=right)
            return node
        if t.kind == "kw" and t.value == "IS":
            self.advance()
            negated = bool(self.accept("kw", "NOT"))
            null_tok = self.expect("kw", "NULL")
            end = null_tok.end
            node = ast.Node(
                ast.BINARY, node.pos, end,
                op="IS NOT NULL" if negated else "IS NULL",
                left=node,
                right=ast.Node(ast.LITERAL, null_tok.pos, null_tok.end,
                               value=None, data_type="any"),
            )
        return node

    def _add(self) -> ast.Node:
        node = self._mul()
        while self.cur.kind == "op" and self.cur.value in ("+", "-"):
            tok = self.advance()
            right = self._mul()
            node = ast.Node(ast.BINARY, node.pos, right.end, op=tok.value, left=node, right=right)
        return node

    def _mul(self) -> ast.Node:
        node = self._unary()
        while self.cur.kind == "op" and self.cur.value in ("*", "/", "%"):
            tok = self.advance()
            right = self._unary()
            node = ast.Node(ast.BINARY, node.pos, right.end, op=tok.value, left=node, right=right)
        return node

    def _unary(self) -> ast.Node:
        if self.cur.kind == "op" and self.cur.value in ("+", "-"):
            tok = self.advance()
            operand = self._unary()
            return ast.Node(ast.UNARY, tok.pos, operand.end, op=tok.value, operand=operand)
        return self._primary()

    def _primary(self) -> ast.Node:
        t = self.cur
        if t.kind == "num":
            self.advance()
            dtype = "integer" if isinstance(t.value, int) else "numeric"
            return ast.Node(ast.LITERAL, t.pos, t.end, value=t.value, data_type=dtype)
        if t.kind == "str":
            self.advance()
            return ast.Node(ast.LITERAL, t.pos, t.end, value=t.value, data_type="text")
        if t.kind == "kw" and t.value in ("TRUE", "FALSE"):
            self.advance()
            return ast.Node(
                ast.LITERAL, t.pos, t.end, value=(t.value == "TRUE"), data_type="boolean"
            )
        if t.kind == "kw" and t.value == "NULL":
            self.advance()
            return ast.Node(ast.LITERAL, t.pos, t.end, value=None, data_type="any")
        if t.kind == "op" and t.value == "(":
            self.advance()
            node = self._or()
            close = self.expect("op", ")")
            node.pos = t.pos
            node.end = close.end
            return node
        if t.kind == "ident":
            self.advance()
            if self.accept("op", "("):
                args: list[ast.Node] = []
                if not (self.cur.kind == "op" and self.cur.value == ")"):
                    args.append(self._or())
                    while self.accept("op", ","):
                        args.append(self._or())
                close = self.expect("op", ")")
                return ast.Node(
                    ast.FUNC, t.pos, close.end, func=t.value, args=args
                )
            return ast.Node(ast.FIELD, t.pos, t.end, name=t.value)
        if t.kind == "eof":
            raise ExpressionError("表达式不完整，缺少操作数", t.pos, t.pos + 1)
        raise ExpressionError(f"此处不应出现 {t.value!r}", t.pos, t.end)


def parse(src: str) -> ast.Node:
    if not src or not src.strip():
        raise ExpressionError("表达式为空", 0, max(len(src or ""), 1))
    return Parser(src).parse()
