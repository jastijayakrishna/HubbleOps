ENDPOINT = "https://api.mockprov.test/v2/campaigns:search"
LEGACY_NAMESPACE = "mockprov.v1.campaigns"


class MockProvClient:
    def __init__(self, token, version="v2"):
        self.token = token
        self.version = version

    def call(self, request):
        return {"endpoint": ENDPOINT, "request": request, "version": self.version}


def build_client(token):
    return MockProvClient(token, version="v2")
