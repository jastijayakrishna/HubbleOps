from client import build_client

ACCOUNT_QUERY = "mockprov_query = resource=accounts fields=accounts.id"


def fetch_accounts(token):
    return build_client(token).call(ACCOUNT_QUERY)
