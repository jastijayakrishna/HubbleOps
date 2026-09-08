from reporting import campaign_identifiers, campaign_labels

ROWS = [
    {"campaigns.id": "11", "campaigns.name": "north"},
    {"campaigns.id": "31", "campaigns.name": "south"},
]


def test_labels_come_back():
    assert campaign_labels(ROWS)


def test_identifiers_come_back():
    assert campaign_identifiers(ROWS)
