from googleads import oauth2


def client():
    return oauth2.GoogleRefreshTokenClient("id", "secret", "token")
