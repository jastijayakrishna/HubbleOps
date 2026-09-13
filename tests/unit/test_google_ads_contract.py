from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from email.message import Message
from io import BytesIO
from typing import Any, cast

import pytest

from hubbleops.core.errors import PackDataError
from hubbleops.packs.google_ads.contract import GoogleAdsContract
from hubbleops.packs.google_ads.transport import GoogleAdsCredentials, GoogleAdsRestTransport


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


def test_oracle_context_binds_account_identity_without_binding_credentials() -> None:
    def contract(customer_id: str, secret: str) -> GoogleAdsContract:
        credentials = GoogleAdsCredentials(
            client_id="client",
            client_secret=secret,
            refresh_token=f"refresh-{secret}",
            developer_token=f"developer-{secret}",
            customer_id=customer_id,
            login_customer_id="999",
        )
        return GoogleAdsContract(transport=GoogleAdsRestTransport(credentials))

    first = contract("111", "first").context_hash()
    assert contract("111", "second").context_hash() == first
    assert contract("222", "first").context_hash() != first


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


def test_default_transport_never_claims_provider_authority() -> None:
    result = GoogleAdsContract().validate(
        {
            "service": "GoogleAdsService",
            "method": "Search",
            "query": "SELECT campaign.id FROM campaign",
        },
        "v25",
    )
    assert result.code == "VALID"
    assert result.authority == "CATALOG"


def test_a_mutate_without_a_transport_has_no_catalog_path() -> None:
    result = GoogleAdsContract().validate(
        {"service": "GoogleAdsService", "method": "Mutate", "request": {"operations": []}},
        "v25",
    )
    assert result.code == "ORACLE_UNAVAILABLE"
    assert result.authority is None


@pytest.mark.parametrize("operations_key", ["mutateOperations", "mutate_operations", "operations"])
@pytest.mark.parametrize("version", ["v19", "v20", "v21", "v22", "v23", "v24", "v25"])
def test_catalog_validates_nested_mutate_shapes_without_credentials(
    operations_key: str,
    version: str,
) -> None:
    result = GoogleAdsContract().validate(
        {
            "service": "GoogleAdsService",
            "method": "Mutate",
            "request": {
                operations_key: [
                    {
                        "campaignOperation": {
                            "create": {
                                "name": "catalog-validated",
                                "status": "ENABLED",
                            }
                        }
                    }
                ]
            },
        },
        version,
    )
    assert result.code == "VALID"
    assert result.authority == "CATALOG"
    assert "protobuf JSON shape only" in result.reason
    assert "business rules" in result.reason


def test_live_authority_outranks_a_catalog_valid_mutate_shape() -> None:
    transport = RecordingTransport()
    result = GoogleAdsContract(transport=transport).validate(
        {
            "service": "GoogleAdsService",
            "method": "Mutate",
            "request": {
                "mutateOperations": [{"campaignOperation": {"remove": "customers/1/campaigns/2"}}]
            },
        },
        "v25",
    )
    assert result.code == "VALID"
    assert result.authority == "LIVE"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "body,code,reason",
    [
        (
            {"mutateOperations": cast(dict[str, Any], {})},
            "INVALID",
            "mutate_operations must be a repeated field",
        ),
        (
            {"mutateOperations": [{"campaignOperation": "not-an-object"}]},
            "INVALID",
            "must be an object",
        ),
        (
            {"mutateOperations": [{"campaignOperation": {"remove": {"id": "wrong"}}}]},
            "INVALID",
            "does not match protobuf JSON scalar string",
        ),
        (
            {
                "partialFailure": "false",
                "mutateOperations": [{"campaignOperation": {"remove": "customers/1/campaigns/2"}}],
            },
            "INVALID",
            "does not match protobuf JSON scalar bool",
        ),
        (
            {"mutateOperations": [{"campaignOperation": {"create": {"name": 7}}}]},
            "INVALID",
            "does not match protobuf JSON scalar string",
        ),
        (
            {"mutateOperations": [{"campaignOperation": {"create": {"status": True}}}]},
            "INVALID",
            "protobuf enum",
        ),
        (
            {"mutateOperations": [{"notRealOperation": {"create": cast(dict[str, Any], {})}}]},
            "UNKNOWN_PROVIDER_CONTRACT",
            "not_real_operation is absent from the message catalog",
        ),
        (
            {"mutateOperations": [{"campaignOperation": {"create": {"notARealField": "wrong"}}}]},
            "UNKNOWN_PROVIDER_CONTRACT",
            "not_a_real_field is absent from the message catalog",
        ),
        (
            {"mutateOperations": [cast(dict[str, Any], {})]},
            "UNKNOWN_PROVIDER_CONTRACT",
            "has no catalog-provable operation shape",
        ),
        (
            {
                "mutateOperations": [
                    {
                        "campaignOperation": {"remove": "customers/1/campaigns/2"},
                        "adGroupOperation": {"remove": "customers/1/adGroups/3"},
                    }
                ]
            },
            "UNKNOWN_PROVIDER_CONTRACT",
            "selects 2 operation messages",
        ),
        (
            {
                "operations": cast(list[Any], []),
                "mutateOperations": cast(list[Any], []),
            },
            "UNKNOWN_PROVIDER_CONTRACT",
            "more than one spelling of mutate_operations",
        ),
    ],
)
def test_catalog_mutate_validation_fails_closed(
    body: Mapping[str, Any], code: str, reason: str
) -> None:
    result = GoogleAdsContract().validate(
        {"service": "GoogleAdsService", "method": "Mutate", "request": body}, "v25"
    )
    assert result.code == code
    assert result.authority is None
    assert reason in result.reason


def test_an_unknown_field_is_still_refused_without_a_transport() -> None:
    result = GoogleAdsContract().validate(
        {
            "service": "GoogleAdsService",
            "method": "Search",
            "query": "SELECT campaign.not_a_real_field FROM campaign",
        },
        "v25",
    )
    assert result.code == "INVALID"
    assert result.authority is None


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


def test_provider_rejection_text_is_preserved_verbatim() -> None:
    reason = '{"error":{"message":"field is not selectable"}}'
    result = GoogleAdsContract(
        transport=RecordingTransport({"valid": False, "provider_error": reason})
    ).validate({"service": "GoogleAdsService", "method": "Mutate", "request": {}}, "v25")
    assert result.code == "INVALID"
    assert result.reason == reason


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
    assert facts[old].change == "REMOVED"
    assert facts[old].after is None
    assert new in facts
    assert facts[new].change == "ADDED"


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


class HttpResponse:
    def __init__(self, payload: bytes, request_id: str = "request-1") -> None:
        self.payload = payload
        self.headers = Message()
        self.headers["request-id"] = request_id

    def read(self, maximum: int) -> bytes:
        return self.payload[:maximum]

    def __enter__(self) -> HttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def credentials() -> GoogleAdsCredentials:
    return GoogleAdsCredentials(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        developer_token="developer",
        customer_id="1234567890",
        login_customer_id="0987654321",
    )


def test_live_transport_is_available_only_with_a_complete_environment() -> None:
    assert GoogleAdsRestTransport.from_environment({}).available is False
    configured = GoogleAdsRestTransport.from_environment(
        {
            "GOOGLE_ADS_CLIENT_ID": "client",
            "GOOGLE_ADS_CLIENT_SECRET": "secret",
            "GOOGLE_ADS_REFRESH_TOKEN": "refresh",
            "GOOGLE_ADS_DEVELOPER_TOKEN": "developer",
            "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890",
        }
    )
    assert configured.available is True
    assert configured.credentials is not None
    assert configured.credentials.customer_id == "1234567890"


def test_live_transport_issues_only_validate_only_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[urllib.request.Request] = []

    def open_request(request: urllib.request.Request, timeout: int) -> HttpResponse:
        calls.append(request)
        if request.full_url.endswith("/oauth2/v3/token"):
            return HttpResponse(b'{"access_token":"access"}')
        return HttpResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    transport = GoogleAdsRestTransport(credentials())
    result = transport.validate(
        service="GoogleAdsService",
        method="Search",
        version="v25",
        request={"query": "SELECT campaign.id FROM campaign", "validate_only": False},
    )
    assert result["valid"] is True
    assert len(calls) == 2
    api_request = calls[1]
    assert api_request.full_url.endswith("/v25/customers/1234567890/googleAds:search")
    assert isinstance(api_request.data, bytes)
    assert json.loads(api_request.data) == {
        "query": "SELECT campaign.id FROM campaign",
        "validateOnly": True,
    }
    assert api_request.get_header("Developer-token") == "developer"
    assert api_request.get_header("Login-customer-id") == "0987654321"


def test_live_transport_returns_the_provider_error_body_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = b'{"error":{"message":"invalid query"}}'
    calls = 0

    def open_request(request: urllib.request.Request, timeout: int) -> HttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse(b'{"access_token":"access"}')
        headers = Message()
        headers["request-id"] = "rejected-1"
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request",
            headers,
            BytesIO(provider_error),
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    result = GoogleAdsRestTransport(credentials()).validate(
        service="GoogleAdsService",
        method="Mutate",
        version="v25",
        request={"operations": []},
    )
    assert result["valid"] is False
    assert result["provider_error"] == provider_error.decode()


def test_live_transport_treats_non_validation_http_errors_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def open_request(request: urllib.request.Request, timeout: int) -> HttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse(b'{"access_token":"access"}')
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "unavailable",
            Message(),
            BytesIO(b'{"error":{"message":"retry"}}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    result = GoogleAdsContract(transport=GoogleAdsRestTransport(credentials())).validate(
        {
            "service": "GoogleAdsService",
            "method": "Mutate",
            "request": {"operations": []},
        },
        "v25",
    )
    assert result.code == "ORACLE_UNAVAILABLE"
    assert result.reason == "Google Ads validation unavailable with HTTP 503"


def test_invalid_customer_ids_are_refused_before_any_request() -> None:
    with pytest.raises(PackDataError, match="only digits and hyphens"):
        GoogleAdsCredentials.from_environment(
            {
                "GOOGLE_ADS_CLIENT_ID": "client",
                "GOOGLE_ADS_CLIENT_SECRET": "secret",
                "GOOGLE_ADS_REFRESH_TOKEN": "refresh",
                "GOOGLE_ADS_DEVELOPER_TOKEN": "developer",
                "GOOGLE_ADS_CUSTOMER_ID": "../../other-customer",
            }
        )
