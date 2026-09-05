from collections.abc import Mapping
from typing import Any, cast

import pytest

from hubbleops.packs.google_ads.contract import GoogleAdsContract


class RecordingTransport:
    available = True

    def __init__(self, response: Mapping[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = {"valid": True} if response is None else response

    def validate(
        self, *, service: str, method: str, version: str, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        assert request["validate_only"] is True
        self.calls.append(
            {"service": service, "method": method, "version": version, "request": dict(request)}
        )
        return self.response


@pytest.mark.parametrize("method", ["Search", "SearchStream", "Mutate"])
@pytest.mark.parametrize("requested_flag", [True, False, None, "false", 0])
def test_no_call_can_disable_validation(method: str, requested_flag: Any) -> None:
    transport = RecordingTransport()
    contract = GoogleAdsContract(transport=transport)
    body = {
        "query": "SELECT campaign.id FROM campaign",
        "validate_only": requested_flag,
        "validateOnly": False,
    }
    result = contract.validate(
        {"service": "GoogleAdsService", "method": method, "request": body}, "v25"
    )
    assert result.code == "VALID"
    assert transport.calls[0]["request"]["validate_only"] is True
    assert "validateOnly" not in transport.calls[0]["request"]
    assert transport.calls[0]["method"] == ("Search" if method == "SearchStream" else method)
    assert body["validate_only"] == requested_flag


def test_default_transport_never_passes() -> None:
    result = GoogleAdsContract().validate(
        {
            "service": "GoogleAdsService",
            "method": "Search",
            "query": "SELECT campaign.id FROM campaign",
        },
        "v25",
    )
    assert result.code == "ORACLE_UNAVAILABLE"


@pytest.mark.parametrize(
    "response,code",
    [
        ({"valid": False}, "INVALID"),
        ({}, "ORACLE_UNAVAILABLE"),
        ({"valid": "true"}, "ORACLE_UNAVAILABLE"),
    ],
)
def test_response_must_explicitly_accept(response: Mapping[str, Any], code: str) -> None:
    contract = GoogleAdsContract(transport=RecordingTransport(response))
    assert (
        contract.validate(
            {"service": "GoogleAdsService", "method": "Mutate", "request": {}}, "v25"
        ).code
        == code
    )


@pytest.mark.parametrize("method", ["MutateAnything", "Get", "Delete", "Searchstream"])
def test_unapproved_operation_cannot_reach_transport(method: str) -> None:
    transport = RecordingTransport()
    result = GoogleAdsContract(transport=transport).validate(
        {"service": "GoogleAdsService", "method": method}, "v25"
    )
    assert result.code == "UNKNOWN_PROVIDER_CONTRACT"
    assert not transport.calls


def test_missing_fields_are_rejected_before_transport_and_quoted_text_is_not_a_field() -> None:
    transport = RecordingTransport()
    contract = GoogleAdsContract(transport=transport)
    request = {
        "service": "GoogleAdsService",
        "method": "Search",
        "query": "SELECT campaign.nonexistent_123 FROM campaign",
    }
    assert contract.validate(request, "v25").code == "INVALID"
    assert not transport.calls
    request["query"] = "SELECT campaign.id FROM campaign WHERE campaign.name = 'example.com'"
    assert contract.validate(request, "v25").code == "VALID"


def test_documented_replacement_is_attached_to_computed_diff() -> None:
    old = (
        "enum_value.enums.audience_insights_dimension.AudienceInsightsDimensionEnum."
        "AudienceInsightsDimension.YOUTUBE_DYNAMIC_LINEUP"
    )
    new = (
        "enum_value.enums.audience_insights_dimension.AudienceInsightsDimensionEnum."
        "AudienceInsightsDimension.YOUTUBE_LINEUP"
    )
    facts = {fact.subject: fact for fact in GoogleAdsContract().diff("v19", "v20").facts}
    assert facts[old].replacement == new
    assert facts[old].result == "VALID"


def test_catalog_and_diff_caches_are_isolated_from_callers() -> None:
    contract = GoogleAdsContract()
    catalog = contract.catalog("v25")
    cast(dict[str, Any], catalog.facts[0].attributes)["caller_mutation"] = True
    assert "caller_mutation" not in contract.catalog("v25").facts[0].attributes

    diff = contract.diff("v19", "v20")
    mutable = next(fact for fact in diff.facts if fact.before is not None)
    cast(dict[str, Any], mutable.before)["caller_mutation"] = True
    fresh = {fact.subject: fact for fact in contract.diff("v19", "v20").facts}[mutable.subject]
    assert fresh.before is not None
    assert "caller_mutation" not in fresh.before
