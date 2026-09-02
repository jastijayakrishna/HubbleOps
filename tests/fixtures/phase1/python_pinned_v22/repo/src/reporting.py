from google.ads.googleads.client import GoogleAdsClient

QUERY = """
    SELECT campaign.id, campaign.name, metrics.impressions
    FROM campaign
    WHERE segments.date DURING LAST_30_DAYS
"""


def load(config_path):
    return GoogleAdsClient.load_from_storage(config_path)


def run(client, customer_id):
    service = client.get_service("GoogleAdsService", version="v22")
    return service.search(customer_id=customer_id, query=QUERY)
