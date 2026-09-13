from lib import API_RELEASE, send


def report(customer_id):
    return send(f"customers/{customer_id}/googleAds:search", API_RELEASE)
