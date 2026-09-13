import requests


def send(path, version):
    return requests.get(f"https://googleads.googleapis.com/{version}/{path}")
