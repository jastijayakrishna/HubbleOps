from __future__ import annotations

from pathlib import Path

import pytest

from hubbleops.core.errors import ToolingFailed
from hubbleops.core.precise import (
    Occurrence,
    descriptor_name,
    is_local,
    package_of,
    read_index,
)
from tests.property.test_precise_index_roundtrip import (
    RawDocument,
    RawOccurrence,
    delimited,
    encode_index,
    field_key,
    scalar,
)

FIXTURE = Path("tests/fixtures/precise/ts_symbol_binding/index.scip")
FIXTURE_PACKAGE = "scip-typescript npm symbol-binding-fixture 1.0.0 "
TRANSPORT_SEND = FIXTURE_PACKAGE + "lib/`transport.ts`/send()."
MAIL_SEND = FIXTURE_PACKAGE + "lib/`mail.ts`/send()."
API_RELEASE = FIXTURE_PACKAGE + "lib/`version.ts`/API_RELEASE."
NODE_FETCH = "scip-typescript npm @types/node 18.19.130 web-globals/`fetch.d.ts`/global/fetch()."
DOM_FETCH = "scip-typescript npm typescript 5.9.3 lib/`lib.dom.d.ts`/fetch()."
DOCUMENTS = (
    "app/notify.ts",
    "app/report.ts",
    "lib/index.ts",
    "lib/mail.ts",
    "lib/transport.ts",
    "lib/version.ts",
)


def fixture_payload() -> bytes:
    return FIXTURE.read_bytes()


def test_fixture_metadata_and_paths_are_normalized() -> None:
    payload = fixture_payload()
    assert b"lib\\mail.ts" in payload
    index = read_index(payload)
    assert index.tool == "scip-typescript"
    assert index.tool_version == "0.4.0"
    assert index.documents == DOCUMENTS
    assert all(index.covers(path) for path in DOCUMENTS)
    assert not index.covers("lib\\mail.ts")
    assert index.counts() == {
        "documents": 6,
        "occurrences": 35,
        "definitions": 16,
        "references": 19,
    }
    assert {path: len(index.occurrences_in(path)) for path in DOCUMENTS} == {
        "app/notify.ts": 5,
        "app/report.ts": 9,
        "lib/index.ts": 5,
        "lib/mail.ts": 6,
        "lib/transport.ts": 8,
        "lib/version.ts": 2,
    }


def test_call_sites_bind_to_the_symbol_their_import_resolves_to() -> None:
    index = read_index(fixture_payload())
    assert index.symbol_at("app/report.ts", 3, 9) == TRANSPORT_SEND
    assert index.symbol_at("app/notify.ts", 3, 9) == MAIL_SEND
    transport_definition = index.definition_of(TRANSPORT_SEND)
    assert transport_definition is not None
    assert transport_definition.path == "lib/transport.ts"
    assert (transport_definition.start_line, transport_definition.start_column) == (0, 16)
    mail_definition = index.definition_of(MAIL_SEND)
    assert mail_definition is not None
    assert mail_definition.path == "lib/mail.ts"
    assert index.defines_in_index(TRANSPORT_SEND)
    assert index.defines_in_index(MAIL_SEND)


def test_references_list_every_non_definition_occurrence() -> None:
    index = read_index(fixture_payload())
    assert index.references(TRANSPORT_SEND) == (
        Occurrence("app/report.ts", 0, 22, 0, 26, TRANSPORT_SEND, 0),
        Occurrence("app/report.ts", 3, 9, 3, 13, TRANSPORT_SEND, 0),
        Occurrence("lib/index.ts", 1, 9, 1, 13, TRANSPORT_SEND, 0),
    )
    assert index.references(MAIL_SEND) == (
        Occurrence("app/notify.ts", 0, 9, 0, 13, MAIL_SEND, 0),
        Occurrence("app/notify.ts", 3, 9, 3, 13, MAIL_SEND, 0),
    )
    assert all(not item.is_definition() for item in index.references(API_RELEASE))


def test_imported_constant_resolves_to_its_declaration() -> None:
    index = read_index(fixture_payload())
    assert index.symbol_at("app/report.ts", 0, 9) == API_RELEASE
    definition = index.definition_of(API_RELEASE)
    assert definition is not None
    assert definition.path == "lib/version.ts"
    assert definition.start_line == 0
    assert definition.is_definition()


def test_external_symbols_are_present_but_never_defined_here() -> None:
    index = read_index(fixture_payload())
    assert index.symbol_at("lib/transport.ts", 1, 9) == NODE_FETCH
    assert {item.symbol for item in index.occurrences_in("lib/transport.ts")} >= {
        NODE_FETCH,
        DOM_FETCH,
    }
    assert index.definition_of(NODE_FETCH) is None
    assert not index.defines_in_index(DOM_FETCH)
    assert package_of(NODE_FETCH) == ("@types/node", "18.19.130")
    assert package_of(DOM_FETCH) == ("typescript", "5.9.3")
    assert package_of(TRANSPORT_SEND) == ("symbol-binding-fixture", "1.0.0")
    assert not is_local(TRANSPORT_SEND)


def test_serialize_is_stable_and_never_carries_the_project_root() -> None:
    payload = fixture_payload()
    assert b"file:///repo" in payload
    first = read_index(payload).serialize()
    second = read_index(payload).serialize()
    assert first == second
    assert first.endswith(b"\n")
    assert b"file:///repo" not in first
    assert b"krish" not in first
    assert b"scratchpad" not in first


def test_package_of_handles_escaped_spaces_and_placeholders() -> None:
    assert package_of("scip-python pip my  package 1.0 module/") == ("my package", "1.0")
    assert package_of("scip-python pip . . module/") is None
    assert package_of("scip-python pip name . module/") == ("name", ".")
    assert package_of("scip-python pip") is None
    assert package_of("local 12") is None
    assert is_local("local 12")
    assert not is_local("localhost pip name 1 x/")


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        (TRANSPORT_SEND, "send"),
        (API_RELEASE, "API_RELEASE"),
        (FIXTURE_PACKAGE + "lib/`x.ts`/Client#search().", "search"),
        (FIXTURE_PACKAGE + "lib/`transport.ts`/send().(path)", "path"),
        (FIXTURE_PACKAGE + "lib/`mail.ts`/", "mail.ts"),
        (FIXTURE_PACKAGE + "lib/`mail.ts`/Box#[T]", "T"),
        (FIXTURE_PACKAGE + "pkg/`odd``name`/", "odd`name"),
        (FIXTURE_PACKAGE + "pkg/macro!", "macro"),
        (FIXTURE_PACKAGE + "pkg/meta:", "meta"),
        (FIXTURE_PACKAGE + "pkg/send(a1b2).", "send"),
        (NODE_FETCH, "fetch"),
        ("local 3", None),
        (FIXTURE_PACKAGE, None),
        ("scip-python pip name", None),
        (FIXTURE_PACKAGE + "pkg/broken", None),
        (FIXTURE_PACKAGE + "pkg/(unclosed", None),
        (FIXTURE_PACKAGE + "pkg/``/", None),
    ],
)
def test_descriptor_name_reads_the_last_descriptor(symbol: str, expected: str | None) -> None:
    assert descriptor_name(symbol) == expected


def test_empty_payload_is_an_empty_index() -> None:
    index = read_index(b"")
    assert index.tool == ""
    assert index.tool_version == ""
    assert index.documents == ()
    assert index.occurrences == ()
    assert index.counts() == {"documents": 0, "occurrences": 0, "definitions": 0, "references": 0}
    assert index.symbol_at("anything", 0, 0) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(bytes([0x0F]), id="wire-type-7"),
        pytest.param(bytes([0x12, 0x64]) + b"ab", id="length-past-end"),
        pytest.param(bytes([0x80]), id="varint-truncated"),
        pytest.param(bytes([0x80] * 10 + [0x01]), id="varint-too-long"),
        pytest.param(bytes([0x00]), id="field-number-zero"),
        pytest.param(field_key(1, 1) + bytes(3), id="fixed64-truncated"),
        pytest.param(delimited(2, scalar(1, 5)), id="relative-path-not-a-string"),
        pytest.param(delimited(2, delimited(1, b"\xff")), id="relative-path-not-utf8"),
        pytest.param(delimited(1, delimited(2, delimited(1, b"\xff"))), id="tool-name-not-utf8"),
        pytest.param(
            delimited(2, delimited(1, b"a.ts") + delimited(2, delimited(1, bytes([1, 2])))),
            id="range-two-elements",
        ),
        pytest.param(
            delimited(
                2, delimited(1, b"a.ts") + delimited(2, delimited(1, bytes([1, 2, 3, 4, 5])))
            ),
            id="range-five-elements",
        ),
        pytest.param(
            delimited(
                2,
                delimited(1, b"a.ts")
                + delimited(2, delimited(1, bytes([0, 0, 0]) + b"\x80\x80\x80\x80\x08")),
            ),
            id="range-exceeds-int32",
        ),
        pytest.param(
            delimited(
                2,
                delimited(1, b"a.ts")
                + delimited(2, delimited(1, bytes([0, 0, 1])) + scalar(3, 2**31)),
            ),
            id="roles-exceed-int32",
        ),
    ],
)
def test_malformed_payloads_fail_closed(payload: bytes) -> None:
    with pytest.raises(ToolingFailed) as caught:
        read_index(payload)
    assert str(caught.value).startswith("TOOLING_FAILED: scip")


def test_truncating_the_real_index_fails_closed() -> None:
    payload = fixture_payload()
    with pytest.raises(ToolingFailed) as caught:
        read_index(payload[:-7])
    assert str(caught.value).startswith("TOOLING_FAILED: scip")


def test_symbol_at_picks_the_narrowest_occurrence() -> None:
    document = RawDocument(
        ".\\src\\nested.ts",
        (
            RawOccurrence((0, 0, 0, 20), "outer", 1),
            RawOccurrence((0, 5, 10), "inner", 0),
            RawOccurrence((0, 5, 2, 3), "spanning", 0),
            RawOccurrence((4, 0, 4, 0), "empty", 1),
        ),
    )
    index = read_index(encode_index("tool", "1", (document, RawDocument("bare.ts", ()))))
    assert index.documents == ("bare.ts", "src/nested.ts")
    assert index.occurrences_in("bare.ts") == ()
    assert index.symbol_at("src/nested.ts", 0, 7) == "inner"
    assert index.symbol_at("src/nested.ts", 0, 2) == "outer"
    assert index.symbol_at("src/nested.ts", 0, 10) == "outer"
    assert index.symbol_at("src/nested.ts", 0, 20) == "spanning"
    assert index.symbol_at("src/nested.ts", 1, 0) == "spanning"
    assert index.symbol_at("src/nested.ts", 2, 2) == "spanning"
    assert index.symbol_at("src/nested.ts", 2, 3) is None
    assert index.symbol_at("src/nested.ts", 4, 0) is None
    assert index.symbol_at("src/nested.ts", 3, 0) is None
    assert index.symbol_at("missing.ts", 0, 0) is None


def test_definition_of_prefers_the_first_definition_in_sorted_order() -> None:
    document = RawDocument(
        "b.ts",
        (
            RawOccurrence((1, 0, 1, 1), "twice", 1),
            RawOccurrence((0, 0, 0, 1), "twice", 1),
            RawOccurrence((2, 0, 2, 1), "twice", 8),
            RawOccurrence((3, 0, 3, 1), "local 4", 8),
            RawOccurrence((1, 0, 1, 1), "twice", 1),
        ),
    )
    index = read_index(encode_index("tool", "1", (document,)))
    assert index.counts() == {"documents": 1, "occurrences": 4, "definitions": 2, "references": 2}
    definition = index.definition_of("twice")
    assert definition is not None
    assert definition.start_line == 0
    assert index.references("twice") == (Occurrence("b.ts", 2, 0, 2, 1, "twice", 8),)
    assert index.definition_of("local 4") is None
    assert not index.defines_in_index("local 4")
    assert index.references("absent") == ()
