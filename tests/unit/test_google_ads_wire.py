import json
from pathlib import Path

import pytest

from hubbleops.packs._protocol import WireObservation
from hubbleops.packs.google_ads.wire import WIRE_SIGNATURE


def test_retained_official_log_corpus() -> None:
    for item in json.loads(Path("tests/fixtures/google_ads/wire_corpus.json").read_bytes()):
        result = WIRE_SIGNATURE.parse(item["path"], item["headers"])
        assert result.observation == WireObservation(
            item["service"], item["method"], item["version"]
        )


@pytest.mark.parametrize(
    "path,service,method,version",
    [
        (
            "/google.ads.googleads.v22.services.GoogleAdsService/SearchStream",
            "GoogleAdsService",
            "SearchStream",
            "v22",
        ),
        (
            "/google.ads.googleads.v25.services.CampaignService/MutateCampaigns",
            "CampaignService",
            "MutateCampaigns",
            "v25",
        ),
        ("/v25/customers/123/googleAds:searchStream", "GoogleAdsService", "SearchStream", "v25"),
        ("/v24/customers/123/googleAds:search?pageSize=10", "GoogleAdsService", "Search", "v24"),
        ("/v25/customers/123/googleAds:mutate", "GoogleAdsService", "Mutate", "v25"),
    ],
)
def test_documented_request_path_forms(path: str, service: str, method: str, version: str) -> None:
    result = WIRE_SIGNATURE.parse(path, {"x-goog-api-client": "gl-python/3.12.0 gapic/31.2.0"})
    assert result.observation == WireObservation(service, method, version)
    assert result.code == "MATCH"


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/v25/",
        "/v25/customers/123/googleAds:searchUnknown",
        "/google.ads.googleads.v25.services.GoogleAdsService/",
        "/v25/customers/123/unrecognized:mutate",
    ],
)
def test_near_matches_are_explicit_unknown(path: str) -> None:
    result = WIRE_SIGNATURE.parse(path, {"x-goog-api-client": "gapic/25.0.0"})
    assert result.code == "UNKNOWN_WIRE_SIGNATURE"
    assert result.observation is None
    assert result.reason


def test_conflicting_path_header_is_unknown() -> None:
    result = WIRE_SIGNATURE.parse(
        "/v24/customers/123/googleAds:search", {":path": "/v25/customers/123/googleAds:search"}
    )
    assert result.code == "UNKNOWN_WIRE_SIGNATURE"
