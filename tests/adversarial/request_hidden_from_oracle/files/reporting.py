from pathlib import Path

from client import build_client


def campaign_query():
    return Path(__file__).with_name("queries.txt").read_text(encoding="utf-8").strip()


def fetch_campaigns(token):
    client = build_client(token)
    return client.call(campaign_query())


def campaign_labels(rows):
    return [row["campaigns.name"] for row in rows]


def campaign_identifiers(rows):
    return [int(row["campaigns.id"]) for row in rows]
