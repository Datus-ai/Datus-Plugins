"""Small typed Tableau scalar parser; never accepts SQL fragments as formulas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlglot import exp

MAX_TOKENS = 2048
MAX_DEPTH = 64
TOKEN = re.compile(
    r"""\s*(?:(\[(?:[^\]]|\]\])+\])|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|(<=|>=|!=|<>|==|[()+*/%,=<>-]))"""
)


@dataclass
class Node:
    kind: str
    value: str
    children: tuple = ()
    depth: int = field(init=False, default=1)

    def __post_init__(self):
        self.depth = 1 + max((c.depth for c in self.children), default=0)
        if self.depth > MAX_DEPTH:
            raise ValueError("Tableau formula AST depth resource limit")


class Parser:
    def __init__(self, text: str):
        self.tokens = []
        pos = 0
        while pos < len(text.rstrip()):
            match = TOKEN.match(text, pos)
            if not match:
                raise ValueError("unsupported Tableau formula token")
            col, string, number, word, symbol = match.groups()
            kind, value = next(
                (k, v)
                for k, v in [
                    ("column", col),
                    ("string", string),
                    ("number", number),
                    ("word", word),
                    ("symbol", symbol),
                ]
                if v is not None
            )
            self.tokens.append((kind, value))
            pos = match.end()
            if len(self.tokens) > MAX_TOKENS:
                raise ValueError("Tableau formula token resource limit")
        self.i = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.i][1].upper() if self.i < len(self.tokens) else ""

    def take(self, expected=None):
        if (
            self.i >= len(self.tokens)
            or expected is not None
            and self.peek() != expected
        ):
            raise ValueError(f"expected Tableau formula {expected or 'expression'}")
        value = self.tokens[self.i]
        self.i += 1
        return value

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ValueError("Tableau formula depth resource limit")
        kind, value = self.take()
        upper = value.upper()
        if upper == "IF":
            branches = []
            while True:
                condition = self.expression()
                self.take("THEN")
                branches.extend([condition, self.expression()])
                if self.peek() != "ELSEIF":
                    break
                self.take("ELSEIF")
            if self.peek() == "ELSE":
                self.take("ELSE")
                otherwise = self.expression()
            else:
                otherwise = Node("word", "NULL")
            self.take("END")
            left = Node("if", "", tuple(branches + [otherwise]))
        elif upper in {"NOT", "-", "+"}:
            left = Node(
                "unary", upper, (self.expression(25 if upper == "NOT" else 60),)
            )
        elif value == "(":
            left = self.expression()
            self.take(")")
        elif kind == "word" and self.peek() == "(":
            self.take("(")
            args = []
            if self.peek() != ")":
                while True:
                    args.append(self.expression())
                    if self.peek() != ",":
                        break
                    self.take(",")
            self.take(")")
            left = Node("call", upper, tuple(args))
        elif kind in {"column", "string", "number"} or upper in {
            "NULL",
            "TRUE",
            "FALSE",
        }:
            left = Node(kind, value)
        else:
            raise ValueError(f"unsupported Tableau formula name {value!r}")
        precedence = {
            "OR": 10,
            "AND": 20,
            "=": 30,
            "==": 30,
            "!=": 30,
            "<>": 30,
            "<": 30,
            ">": 30,
            "<=": 30,
            ">=": 30,
            "+": 40,
            "-": 40,
            "*": 50,
            "/": 50,
            "%": 50,
        }
        while self.peek() in precedence and precedence[self.peek()] >= minimum:
            op = self.take()[1].upper()
            left = Node("binary", op, (left, self.expression(precedence[op] + 1)))
        self.depth -= 1
        return left


def quote(name):
    return exp.Identifier(this=name, quoted=True).sql()


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return exp.Literal.string(str(value)).sql()


def string_value(value):
    # Tableau permits backslash escaping; SQL quoting is only emitted after decoding.
    if re.search(r"""\\(?![\\"'])""", value[1:-1]):
        raise ValueError("unsupported Tableau string escape")
    return re.sub(r'\\([\\"\'])', r"\1", value[1:-1])


def translate(text: str, columns: dict[str, str], dialect="duckdb") -> tuple[str, str]:
    """Return a validated scalar expression and its bounded inferred result type."""
    if dialect != "duckdb":
        raise ValueError(
            "native Tableau formulas currently require DuckDB semantic mapping"
        )
    if not isinstance(text, str) or len(text.encode()) > 256 * 1024:
        raise ValueError("invalid or oversized Tableau formula")
    parser = Parser(text)
    tree = parser.expression()
    if parser.i != len(parser.tokens):
        raise ValueError("trailing Tableau formula syntax")

    def emit(node):
        if node.kind == "column":
            name = node.value[1:-1].replace("]]", "]")
            if name not in columns:
                raise ValueError(f"unknown Tableau field {name!r}")
            return quote(name), columns[name]
        if node.kind == "string":
            return literal(string_value(node.value)), "string"
        if node.kind == "number":
            return node.value, "real" if "." in node.value else "integer"
        if node.kind == "word":
            return (
                node.value.upper(),
                "null" if node.value.upper() == "NULL" else "boolean",
            )
        args = [emit(child) for child in node.children]
        if node.kind == "if":
            types = {t for _, t in args[1::2]} | {args[-1][1]}
            types.discard("null")
            if len(types) > 1:
                raise ValueError("incompatible Tableau IF result types")
            return "CASE " + " ".join(
                f"WHEN {args[i][0]} THEN {args[i + 1][0]}"
                for i in range(0, len(args) - 1, 2)
            ) + f" ELSE {args[-1][0]} END", next(iter(types), "null")
        if node.kind == "unary":
            allowed = (
                {"boolean", "null"}
                if node.value == "NOT"
                else {"integer", "real", "null"}
            )
            if args[0][1] not in allowed:
                raise ValueError("unsupported Tableau unary operand type")
            return (
                f"({node.value} {args[0][0]})",
                "boolean" if node.value == "NOT" else args[0][1],
            )
        if node.kind == "binary":
            (left, lt), (right, rt) = args
            op = node.value
            if op in {"AND", "OR"} and any(
                t not in {"boolean", "null"} for t in (lt, rt)
            ):
                raise ValueError("Tableau logical operators require Boolean operands")
            if op == "+" and lt == rt == "string":
                op = "||"
            elif op in {"+", "-", "*", "/", "%"} and (
                lt not in {"integer", "real", "null"}
                or rt not in {"integer", "real", "null"}
            ):
                raise ValueError("unsupported Tableau arithmetic operand types")
            return f"({left} {'=' if op == '==' else op} {right})", (
                "boolean"
                if node.value
                in {"OR", "AND", "=", "==", "!=", "<>", "<", ">", "<=", ">="}
                else "string"
                if op == "||"
                else "real"
                if op == "/" or "real" in {lt, rt}
                else "integer"
            )
        name = node.value
        a = [v for v, _ in args]
        arity = {
            "STR": 1,
            "DATE": 1,
            "DATEPARSE": 2,
            "ISNULL": 1,
            "IFNULL": 2,
            "YEAR": 1,
            "MONTH": 1,
            "DAY": 1,
            "TRIM": 1,
            "SPLIT": 3,
            "DATEDIFF": 3,
        }
        if name not in arity or len(args) != arity[name]:
            raise ValueError(f"unsupported Tableau function or arity {name}")
        if name == "STR":
            if args[0][1] == "boolean":
                return (
                    f"CASE WHEN {a[0]} THEN '1' WHEN NOT {a[0]} THEN '0' ELSE NULL END",
                    "string",
                )
            if args[0][1] not in {"integer", "string", "null"}:
                raise ValueError(
                    "STR conversion requires verified integer, boolean or string input semantics"
                )
            return f"CAST({a[0]} AS VARCHAR)", "string"
        if name == "DATE":
            if args[0][1] not in {"date", "datetime", "null"}:
                raise ValueError(
                    "Tableau DATE automatic string parsing requires an explicit input-domain mapping"
                )
            return f"TRY_CAST({a[0]} AS DATE)", "date"
        if name == "DATEPARSE":
            if args[1][1] not in {"string", "null"}:
                raise ValueError("DATEPARSE requires a string input")
            if (
                node.children[0].kind != "string"
                or string_value(node.children[0].value) != "dd/MM/yyyy"
            ):
                raise ValueError("DATEPARSE supports only explicit dd/MM/yyyy pattern")
            return f"TRY_STRPTIME({a[1]}, '%d/%m/%Y')", "datetime"
        if name == "ISNULL":
            return f"({a[0]} IS NULL)", "boolean"
        if name == "IFNULL":
            types = {args[0][1], args[1][1]} - {"null"}
            if len(types) > 1 and not types <= {"integer", "real"}:
                raise ValueError("incompatible Tableau IFNULL result types")
            result_type = "real" if "real" in types else next(iter(types), "null")
            return f"COALESCE({a[0]}, {a[1]})", result_type
        if name in {"YEAR", "MONTH", "DAY"}:
            if args[0][1] not in {"date", "datetime", "null"}:
                raise ValueError("date component requires a date or datetime input")
            return f"{name}({a[0]})", "integer"
        if name == "TRIM":
            if args[0][1] not in {"string", "null"}:
                raise ValueError("TRIM requires a string input")
            return f"TRIM({a[0]})", "string"
        if name == "SPLIT":
            if args[0][1] not in {"string", "null"}:
                raise ValueError("SPLIT requires a string input")
            token = node.children[2]
            if token.kind != "number" or "." in token.value or int(token.value) <= 0:
                raise ValueError("SPLIT currently requires a positive integer token")
            if node.children[1].kind != "string" or not string_value(
                node.children[1].value
            ):
                raise ValueError("SPLIT requires a nonempty literal separator")
            return f"SPLIT_PART({a[0]}, {a[1]}, {a[2]})", "string"
        if name == "DATEDIFF":
            if any(t not in {"date", "datetime", "null"} for _, t in args[1:]):
                raise ValueError("DATEDIFF requires date or datetime inputs")
            if (
                node.children[0].kind != "string"
                or string_value(node.children[0].value).lower() != "day"
            ):
                raise ValueError("DATEDIFF currently supports day only")
            return f"DATE_DIFF('day', {a[1]}, {a[2]})", "integer"
        raise ValueError("unsupported Tableau expression")

    return emit(tree)
