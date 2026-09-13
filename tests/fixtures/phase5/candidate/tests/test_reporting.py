from billing import total_spend
from reporting import campaign_identifiers, campaign_labels, fetch_campaigns

ROWS = [
    {"campaigns.id": "11", "campaigns.legacy": "north", "campaigns.name": "north"},
    {"campaigns.id": "31", "campaigns.legacy": "south", "campaigns.name": "south"},
]


def test_labels_come_back_in_row_order():
    assert campaign_labels(ROWS) == ["north", "south"]


def test_identifiers_are_integers():
    assert campaign_identifiers(ROWS) == [11, 31]


def test_spend_multiplies_every_identifier():
    assert total_spend(ROWS, 2) == 84


def test_spend_of_no_rows_is_zero():
    assert total_spend([], 5) == 0


def test_a_request_names_the_campaigns_resource():
    result = fetch_campaigns("token")
    assert "campaigns" in result["request"]
    assert result["version"]
