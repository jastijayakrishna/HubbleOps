from client import build_client

CAMPAIGN_QUERY = (
    "mockprov_query = resource=campaigns fields=campaigns.id,campaigns.name,campaigns.legacy"
)


def fetch_campaigns(token):
    client = build_client(token)
    return client.call(CAMPAIGN_QUERY)


def campaign_labels(rows):
    return [row["campaigns.name"] for row in rows]


def campaign_identifiers(rows):
    return [int(row["campaigns.id"]) for row in rows]
