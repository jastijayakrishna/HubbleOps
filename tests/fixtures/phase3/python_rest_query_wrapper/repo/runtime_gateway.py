import requests

API_RELEASE = "v19"


def fetch_window(account_id: str, day_count: int) -> object:
    query = f"""
        SELECT campaign.id, metrics.clicks
        FROM campaign
        WHERE segments.date DURING LAST_{day_count}_DAYS
    """
    endpoint = (
        f"https://googleads.googleapis.com/{API_RELEASE}/customers/{account_id}/googleAds:search"
    )
    payload = {"query": query}
    return requests.post(endpoint, json=payload)
