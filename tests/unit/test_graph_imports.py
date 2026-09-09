from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.core.errors import ToolingFailed
from hubbleops.graph.imports import (
    AstGrep,
    build,
    combinable_queries,
    queries_for,
    separate_queries,
)

COMBINED_LANGUAGE_FIXTURES = (
    ("python", "tests/fixtures/phase3/wrapper_patterns/repo", ("wrappers.py",)),
    (
        "typescript",
        "tests/fixtures/phase3/typescript_version_symbol/repo",
        ("release_value.ts", "transport_lookup.ts"),
    ),
)


def test_python_imports_connect_to_local_definition_and_serialize_deterministically() -> None:
    root = Path("tests/fixtures/phase3/python_imported_wrapper/repo")
    paths = {"python": ("provider_bridge.py", "scheduled_report.py")}
    first = build(root, paths, AstGrep())
    second = build(root, paths, AstGrep())
    assert first.serialize() == second.serialize()
    assert {definition.name for definition in first.definitions} == {
        "run_report",
        "submit_report",
    }
    imported = next(item for item in first.imports if "submit_report" in item.symbols)
    assert imported.target_path == "provider_bridge.py"
    definition = next(item for item in first.definitions if item.name == "submit_report")
    assert definition.parameters == ("account_id", "query")
    assert {call.path for call in first.callers(definition)} == {"scheduled_report.py"}


def test_typescript_imports_connect_to_exported_assignment() -> None:
    root = Path("tests/fixtures/phase3/typescript_version_symbol/repo")
    paths = {"typescript": ("release_value.ts", "transport_lookup.ts")}
    graph = build(root, paths, AstGrep())
    imported = next(item for item in graph.imports if "API_RELEASE" in item.symbols)
    assert imported.target_path == "release_value.ts"
    assignment = next(item for item in graph.assignments if item.target.text == "API_RELEASE")
    assert assignment.value.text == '"v24"'


def test_typescript_parent_import_normalizes_before_local_resolution() -> None:
    root = Path("tests/fixtures/phase3/typescript_parent_import/repo")
    paths = {
        "typescript": (
            "lib/version_token.ts",
            "tasks/proto_registry.ts",
        )
    }
    graph = build(root, paths, AstGrep())
    imported = next(item for item in graph.imports if "VERSION_TOKEN" in item.symbols)
    assert imported.module == "../lib/version_token"
    assert imported.target_path == "lib/version_token.ts"


def test_wrapper_corpus_enters_definition_class_decorator_and_registration_graph() -> None:
    root = Path("tests/fixtures/phase3/wrapper_patterns/repo")
    graph = build(root, {"python": ("wrappers.py",)}, AstGrep())
    names = {item.name for item in graph.definitions}
    assert {
        "async_wrapper",
        "configured_wrapper",
        "decorated_wrapper",
        "depth_6",
        "direct_wrapper",
        "execute",
        "factory_registry",
        "intermediate_hop",
        "send",
        "transmit",
    } <= names
    assert {item.name for item in graph.classes} >= {
        "Adapter",
        "Gateway",
        "SearchAdapter",
        "StreamAdapter",
    }
    assert graph.decorators
    assert graph.registrations
    decorated = next(item for item in graph.definitions if item.name == "decorated_wrapper")
    search = next(
        item
        for item in graph.definitions
        if item.name == "execute" and item.parameters == ("self", "account_id", "query")
    )
    assert "decorator traced" in graph.wrapper_context(decorated)
    search_context = graph.wrapper_context(search)
    assert any(item.startswith("class ") for item in search_context)
    assert any(item.startswith("registration ") for item in search_context)
    assert any(item.startswith("factory factory_registry") for item in search_context)


@pytest.mark.parametrize(("language", "root", "paths"), COMBINED_LANGUAGE_FIXTURES)
def test_one_combined_pass_returns_exactly_what_separate_queries_return(
    language: str, root: str, paths: tuple[str, ...]
) -> None:
    runner = AstGrep()
    target = Path(root)

    sequential: set[object] = set()
    for query in queries_for(language):
        sequential |= set(runner.query(target, paths, language, query))

    combined = set(runner.query_all(target, paths, language, combinable_queries(language)))
    for query in separate_queries(language):
        combined |= set(runner.query(target, paths, language, query))

    assert combined == sequential
    assert sequential


@pytest.mark.parametrize("language", ("javascript", "php", "python", "typescript"))
def test_every_query_the_partition_calls_combinable_is_accepted_by_the_rule_engine(
    language: str,
) -> None:
    runner = AstGrep()
    root = Path("tests/fixtures/phase3/wrapper_patterns/repo")
    combinable = combinable_queries(language)

    assert combinable
    runner.query_all(root, ("wrappers.py",), language, combinable)


@pytest.mark.parametrize("language", ("javascript", "php", "typescript"))
def test_every_query_the_partition_holds_back_is_still_rejected_by_the_rule_engine(
    language: str,
) -> None:
    runner = AstGrep()
    root = Path("tests/fixtures/phase3/wrapper_patterns/repo")
    held_back = separate_queries(language)

    assert held_back
    for query in held_back:
        with pytest.raises(ToolingFailed):
            runner.query_all(root, ("wrappers.py",), language, (query,))
