ENDPOINT = "https://api.mockprov.test/v1/campaigns:search"


class MockProvClient:
    def __init__(self, token, version="v1"):
        self.token = token
        self.version = version

    def call(self, request):
        return {"endpoint": ENDPOINT, "request": request, "version": self.version}


def build_client(token):
    return MockProvClient(token, version="v1")
