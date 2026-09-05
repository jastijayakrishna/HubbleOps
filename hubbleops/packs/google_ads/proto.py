from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\b[A-Za-z_][A-Za-z0-9_.]*\b|-?\d+|[^\s]')
SCALARS = frozenset(
    {
        "double",
        "float",
        "int32",
        "int64",
        "uint32",
        "uint64",
        "sint32",
        "sint64",
        "fixed32",
        "fixed64",
        "sfixed32",
        "sfixed64",
        "bool",
        "string",
        "bytes",
    }
)


@dataclass(frozen=True)
class Symbol:
    kind: str
    name: str
    attributes: Mapping[str, str | int | bool]


def symbols(source: str) -> tuple[str, tuple[Symbol, ...]]:
    text = re.sub(
        r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
        lambda m: m[0] if m[0].startswith('"') else " ",
        source,
        flags=re.S,
    )
    tokens = TOKEN.findall(text)
    package = ""
    scopes: list[tuple[str, str]] = []
    result: list[Symbol] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "package":
            package = tokens[index + 1]
        if token in {"message", "enum", "service", "oneof"} and tokens[index + 2] == "{":
            name = tokens[index + 1]
            qualified = ".".join(
                [name for kind, name in scopes if kind in {"message", "enum", "service"}] + [name]
            )
            if token != "oneof":
                result.append(Symbol(token, qualified, {}))
            scopes.append((token, name))
            index += 3
            continue
        if token == "{":
            scopes.append(("option", ""))
        elif token == "}":
            if not scopes:
                raise ValueError("unbalanced proto scope")
            scopes.pop()
        elif (
            scopes
            and scopes[-1][0] == "enum"
            and index + 2 < len(tokens)
            and tokens[index + 1] == "="
            and re.fullmatch(r"-?\d+", tokens[index + 2])
        ):
            parent = ".".join(name for kind, name in scopes if kind in {"message", "enum"})
            result.append(
                Symbol("enum_value", f"{parent}.{token}", {"number": int(tokens[index + 2])})
            )
        elif scopes and scopes[-1][0] in {"message", "oneof"}:
            repeated = token == "repeated"
            begin = index + 1 if token in {"optional", "required", "repeated"} else index
            if begin + 3 < len(tokens) and tokens[begin + 2] == "=" and tokens[begin + 3].isdigit():
                proto_type, name = tokens[begin : begin + 2]
                parent = ".".join(name for kind, name in scopes if kind == "message")
                result.append(
                    Symbol(
                        "proto_field",
                        f"{parent}.{name}",
                        {
                            "proto_type": proto_type,
                            "number": int(tokens[begin + 3]),
                            "repeated": repeated,
                        },
                    )
                )
                index = begin + 4
                continue
        index += 1
    if scopes or not package:
        raise ValueError("incomplete proto file")
    identities = [(item.kind, item.name) for item in result]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate proto symbol")
    return package, tuple(result)


def resolve_type(type_name: str, owner: str, known: Mapping[str, Symbol]) -> str:
    if type_name in SCALARS:
        return type_name
    if type_name.lstrip(".") in known:
        return type_name.lstrip(".")
    parts = owner.split(".")
    while parts:
        candidate = ".".join([*parts, type_name])
        if candidate in known:
            return candidate
        parts.pop()
    return type_name
