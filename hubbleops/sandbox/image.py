from __future__ import annotations

import re
from dataclasses import dataclass

from hubbleops.core.canonical import content_id
from hubbleops.core.errors import HubbleOpsError

DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")


class ImageInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class ImageSpec:
    reference: str
    user: str
    entrypoint: str | None = None

    def __post_init__(self) -> None:
        if DIGEST_IMAGE.fullmatch(self.reference) is None:
            raise ImageInvalid(f"container image must be pinned by sha256 digest: {self.reference}")
        if re.fullmatch(r"[0-9]+:[0-9]+", self.user) is None:
            raise ImageInvalid(f"container user must be a numeric uid:gid: {self.user}")
        if self.entrypoint is not None and not self.entrypoint.startswith("/"):
            raise ImageInvalid("container entrypoint must be an absolute path")

    def fingerprint(self) -> str:
        return content_id(
            {"reference": self.reference, "user": self.user, "entrypoint": self.entrypoint}
        )


CAPTURE_IMAGE = ImageSpec(
    "docker.io/library/python@sha256:9381e50cc82f4279b949fcd2d2f5e57cf97b1da2399eb956502364ceea2f4e83",
    "65532:65532",
)
NODE_CAPTURE_IMAGE = ImageSpec(
    "docker.io/library/node@sha256:8d6a8a2f25f8950664594b4c64678909718af0f567c99a85e44b11d0b44e059d",
    "65532:65532",
)
PHP_CAPTURE_IMAGE = ImageSpec(
    "docker.io/library/php@sha256:2f2e706ad96f295e2bc2b36dcc88e7413b7270fcd2b1532f3cfb4b6ebeca74ca",
    "65532:65532",
)
PROXY_IMAGE = ImageSpec(
    "docker.io/mitmproxy/mitmproxy@sha256:00b77b5d8804c8ad18cb6caefbf9d5849e895e8986c5ce011f4ae30f4385962f",
    "1000:1000",
    "/usr/local/bin/python",
)

__all__ = [
    "CAPTURE_IMAGE",
    "NODE_CAPTURE_IMAGE",
    "PHP_CAPTURE_IMAGE",
    "PROXY_IMAGE",
    "ImageInvalid",
    "ImageSpec",
]
