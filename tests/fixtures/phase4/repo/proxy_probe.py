from __future__ import annotations

import urllib.request

request = urllib.request.Request(
    "https://fixture-service:8443/v22/customers/1/googleAds:search",
    data=b"SELECT campaign.id FROM campaign",
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 204:
        raise RuntimeError(f"fixture returned {response.status}")
