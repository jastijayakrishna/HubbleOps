import pytest

from hubbleops.packs._protocol import WireObservation
from hubbleops.packs.google_ads.telemetry import TELEMETRY


@pytest.mark.parametrize("header", ["Method", "method", "metric.labels.method"])
def test_console_csv_rows_are_accounted_for(header: str) -> None:
    result = TELEMETRY.parse(
        f'{header},Count\n"google.ads.googleads.v25.services.GoogleAdsService.Search",7\ninvalid,2\n,1\n'
    )
    assert result.observations == (WireObservation("GoogleAdsService", "Search", "v25"),)
    assert [issue.row for issue in result.issues] == [3, 4]
    assert all(issue.reason for issue in result.issues)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "Count\n1\n",
        "Method,method\na,b\n",
        "Method,Count,Count\na,1,2\n",
        'Method\n"unterminated',
        'Method,Count\n"google.ads.googleads.v25.services.GoogleAdsService.Search,7',
        'Method,Count\n"google.ads.googleads.v25.services.GoogleAdsService.Search",7,extra\n',
        'Method,Count\n"google.ads.googleads.v25.services.GoogleAdsService.Search"\n',
    ],
)
def test_unusable_csv_has_an_explicit_issue(payload: str) -> None:
    result = TELEMETRY.parse(payload)
    assert not result.observations
    assert result.issues
