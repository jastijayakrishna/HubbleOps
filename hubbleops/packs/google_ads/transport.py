from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hubbleops.core.errors import PackDataError
from hubbleops.core.records import as_mapping, as_text, is_mapping, parse_json

OAUTH_URL = "https://www.googleapis.com/oauth2/v3/token"
API_ROOT = "https://googleads.googleapis.com"
HTTP_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 4_194_304


@dataclass(frozen=True, slots=True)
class GoogleAdsCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    developer_token: str
    customer_id: str
    login_customer_id: str | None

    @staticmethod
    def from_environment(
        environment: Mapping[str, str] | None = None,
    ) -> GoogleAdsCredentials | None:
        source = os.environ if environment is None else environment
        required = {
            key: source.get(key, "").strip()
            for key in (
                "GOOGLE_ADS_CLIENT_ID",
                "GOOGLE_ADS_CLIENT_SECRET",
                "GOOGLE_ADS_REFRESH_TOKEN",
                "GOOGLE_ADS_DEVELOPER_TOKEN",
                "GOOGLE_ADS_CUSTOMER_ID",
            )
        }
        if not all(required.values()):
            return None
        login = source.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
        return GoogleAdsCredentials(
            client_id=required["GOOGLE_ADS_CLIENT_ID"],
            client_secret=required["GOOGLE_ADS_CLIENT_SECRET"],
            refresh_token=required["GOOGLE_ADS_REFRESH_TOKEN"],
            developer_token=required["GOOGLE_ADS_DEVELOPER_TOKEN"],
            customer_id=_customer_id(required["GOOGLE_ADS_CUSTOMER_ID"]),
            login_customer_id=_customer_id(login) if login else None,
        )


class GoogleAdsRestTransport:
    def __init__(self, credentials: GoogleAdsCredentials | None) -> None:
        self.credentials = credentials
        self._access_token: str | None = None

    @staticmethod
    def from_environment(
        environment: Mapping[str, str] | None = None,
    ) -> GoogleAdsRestTransport:
        return GoogleAdsRestTransport(GoogleAdsCredentials.from_environment(environment))

    @property
    def available(self) -> bool:
        return self.credentials is not None

    def validate(
        self,
        *,
        service: str,
        method: str,
        version: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        credentials = self.credentials
        if credentials is None:
            raise PackDataError("Google Ads credentials are incomplete")
        if service != "GoogleAdsService" or method not in ("Search", "Mutate"):
            raise PackDataError(f"{service}.{method} has no approved REST validation route")
        body = dict(request)
        body.pop("validate_only", None)
        body["validateOnly"] = True
        url = f"{API_ROOT}/{version}/customers/{credentials.customer_id}/googleAds:{method.lower()}"
        headers = {
            "Authorization": f"Bearer {self._token(credentials)}",
            "Content-Type": "application/json",
            "developer-token": credentials.developer_token,
        }
        if credentials.login_customer_id is not None:
            headers["login-customer-id"] = credentials.login_customer_id
        call = urllib.request.Request(
            url,
            data=json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(call, timeout=HTTP_TIMEOUT_SECONDS) as response:
                payload = _read_response(response)
                return {
                    "valid": True,
                    "request_id": response.headers.get("request-id"),
                    "response": _json_mapping(payload),
                }
        except urllib.error.HTTPError as error:
            payload = _read_response(error)
            if error.code != 400:
                raise PackDataError(
                    f"Google Ads validation unavailable with HTTP {error.code}"
                ) from error
            return {
                "valid": False,
                "status": error.code,
                "request_id": error.headers.get("request-id"),
                "provider_error": payload.decode("utf-8", errors="replace"),
            }

    def _token(self, credentials: GoogleAdsCredentials) -> str:
        if self._access_token is not None:
            return self._access_token
        payload = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "refresh_token": credentials.refresh_token,
            }
        ).encode("ascii")
        request = urllib.request.Request(
            OAUTH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                token = as_text(_json_mapping(_read_response(response)).get("access_token"))
        except urllib.error.HTTPError as error:
            raise PackDataError(f"OAuth token exchange failed with HTTP {error.code}") from error
        if not token:
            raise PackDataError("OAuth token exchange returned no access token")
        self._access_token = token
        return token


def _customer_id(value: str) -> str:
    normalized = value.replace("-", "")
    if not normalized.isdigit():
        raise PackDataError("Google Ads customer ids must contain only digits and hyphens")
    return normalized


def _read_response(response: Any) -> bytes:
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise PackDataError(f"Google Ads response exceeds {MAX_RESPONSE_BYTES} bytes")
    return payload


def _json_mapping(payload: bytes) -> Mapping[str, Any]:
    parsed = parse_json(payload, MAX_RESPONSE_BYTES)
    if not parsed.ok() or not is_mapping(parsed.value):
        raise PackDataError(
            f"Google Ads returned no JSON object: {parsed.reason or 'empty object'}"
        )
    return as_mapping(parsed.value)


__all__ = [
    "API_ROOT",
    "HTTP_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "OAUTH_URL",
    "GoogleAdsCredentials",
    "GoogleAdsRestTransport",
]
