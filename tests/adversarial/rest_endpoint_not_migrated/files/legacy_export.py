import urllib.request

EXPORT_ENDPOINT = "https://api.mockprov.test/v1/campaigns:export"


def export_campaigns(token):
    request = urllib.request.Request(EXPORT_ENDPOINT, headers={"authorization": token})
    return request.full_url
