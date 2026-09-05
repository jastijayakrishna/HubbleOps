import requests


def submit_report(account_id: str, query: str) -> object:
    endpoint = f"https://googleads.googleapis.com/v24/customers/{account_id}/googleAds:search"
    return requests.post(endpoint, json={"query": query})
