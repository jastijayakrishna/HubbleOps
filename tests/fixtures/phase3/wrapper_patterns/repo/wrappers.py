import os

import requests

API_VERSION = "v24"


def transmit(account_id: str, query: str, version: str) -> object:
    endpoint = f"https://googleads.googleapis.com/{version}/customers/{account_id}/googleAds:search"
    return requests.post(endpoint, json={"query": query})


def direct_wrapper(account_id: str, query: str) -> object:
    return transmit(account_id, query, API_VERSION)


class Gateway:
    def send(self, account_id: str, query: str) -> object:
        return direct_wrapper(account_id, query)


class Adapter:
    def execute(self, account_id: str, query: str) -> object:
        raise NotImplementedError


class SearchAdapter(Adapter):
    def execute(self, account_id: str, query: str) -> object:
        return direct_wrapper(account_id, query)


class StreamAdapter(Adapter):
    def execute(self, account_id: str, query: str) -> object:
        return direct_wrapper(account_id, query)


REGISTRY = {"search": SearchAdapter, "stream": StreamAdapter}


def factory_registry(kind: str) -> type[Adapter]:
    return REGISTRY[kind]


def dependency_injection_entry(adapter: Adapter, account_id: str, query: str) -> object:
    return adapter.execute(account_id, query)


def traced(function: object) -> object:
    return function


@traced
def decorated_wrapper(account_id: str, query: str) -> object:
    return direct_wrapper(account_id, query)


async def async_wrapper(account_id: str, query: str) -> object:
    return direct_wrapper(account_id, query)


async def invoke_async(account_id: str, query: str) -> object:
    return await async_wrapper(account_id, query)


async def scheduled_async(account_id: str) -> object:
    query = "SELECT campaign.id FROM campaign"
    return await invoke_async(account_id, query)


def configured_wrapper(account_id: str, query: str) -> object:
    version = os.getenv("GOOGLE_ADS_API_VERSION")
    return transmit(account_id, query, version)


def intermediate_hop(account_id: str, query: str) -> object:
    return direct_wrapper(account_id, query)


def depth_1(account_id: str, query: str) -> object:
    return depth_2(account_id, query)


def depth_2(account_id: str, query: str) -> object:
    return depth_3(account_id, query)


def depth_3(account_id: str, query: str) -> object:
    return depth_4(account_id, query)


def depth_4(account_id: str, query: str) -> object:
    return depth_5(account_id, query)


def depth_5(account_id: str, query: str) -> object:
    return depth_6(account_id, query)


def depth_6(account_id: str, query: str) -> object:
    return direct_wrapper(account_id, query)


def publish(payload: object) -> object:
    return payload


def boundary_wrapper(query: str) -> object:
    return publish({"query": query})


def invoke_patterns(account_id: str) -> tuple[object, ...]:
    query = "SELECT campaign.id FROM campaign"
    adapter = factory_registry("search")()
    return (
        Gateway().send(account_id, query),
        dependency_injection_entry(adapter, account_id, query),
        decorated_wrapper(account_id, query),
        configured_wrapper(account_id, query),
        intermediate_hop(account_id, query),
        depth_1(account_id, query),
        boundary_wrapper(query),
    )
