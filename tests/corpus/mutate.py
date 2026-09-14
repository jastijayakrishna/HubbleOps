from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tests.corpus.spec import Family, Label, normalise_path

SEED = "hubbleops-corpus-2026-09-14"
SEEDED_VERSION = "v21"
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "target",
        "coverage",
        ".next",
        ".nuxt",
    }
)
LANGUAGE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "php": (".php",),
    "typescript": (".ts", ".tsx"),
    "javascript": (".js", ".mjs", ".cjs"),
}
IDENTIFIERS = (
    "google-ads",
    "googleads",
    "google_ads",
    "GoogleAds",
    "google.ads",
)


@dataclass(frozen=True)
class Carrier:
    name: str
    languages: tuple[str, ...]
    mechanism: str
    template: str


CARRIERS: tuple[Carrier, ...] = (
    Carrier(
        "rest_endpoint_version",
        ("python",),
        "endpoint_path",
        'HUBBLEOPS_SEEDED_ENDPOINT = "https://googleads.googleapis.com/{version}/customers/"',
    ),
    Carrier(
        "python_namespace_version",
        ("python",),
        "namespace_segment",
        'HUBBLEOPS_SEEDED_NAMESPACE = "google.ads.googleads.{version}.services"',
    ),
    Carrier(
        "python_service_version",
        ("python",),
        "version_literal",
        'HOPS_SEEDED_SVC = \'get_service("GoogleAdsService", version="{version}")\'',
    ),
    Carrier(
        "python_client_version",
        ("python",),
        "version_literal",
        "HUBBLEOPS_SEEDED_CLIENT_CALL = 'GoogleAdsClient.load_from_storage(version=\"{version}\")'",
    ),
    Carrier(
        "config_literal_version",
        ("python",),
        "config_version",
        'GOOGLE_ADS_API_VERSION = "{version}"',
    ),
    Carrier(
        "php_namespace_version",
        ("php",),
        "namespace_segment",
        "$hopsSeededNs = 'Google\\Ads\\GoogleAds\\{VERSION}\\Services';",
    ),
    Carrier(
        "rest_path_version",
        ("php",),
        "endpoint_path",
        "$hubbleopsSeededRestPath = '/{version}/customers/';",
    ),
    Carrier(
        "config_literal_version",
        ("php",),
        "config_version",
        "$GOOGLE_ADS_API_VERSION = '{version}';",
    ),
    Carrier(
        "node_api_version",
        ("typescript", "javascript"),
        "version_literal",
        'export const hubbleopsSeededClientOptions = {{ apiVersion: "{version}" }};',
    ),
    Carrier(
        "rest_endpoint_version",
        ("typescript", "javascript"),
        "endpoint_path",
        "export const hubbleopsSeededEndpoint = "
        '"https://googleads.googleapis.com/{version}/customers/";',
    ),
    Carrier(
        "proto_namespace_version",
        ("typescript", "javascript"),
        "namespace_segment",
        'export const hubbleopsSeededNamespace = "google.ads.googleads.{version}.services";',
    ),
    Carrier(
        "config_literal_version",
        ("typescript", "javascript"),
        "config_version",
        'export const GOOGLE_ADS_API_VERSION = "{version}";',
    ),
)


@dataclass(frozen=True)
class Mutation:
    carrier: str
    mechanism: str
    path: str
    line: int
    text: str
    version: str


@dataclass(frozen=True)
class MutationPlan:
    mutations: tuple[Mutation, ...]
    skipped: tuple[str, ...]


def _suffixes(language: str) -> tuple[str, ...]:
    lowered = language.strip().lower()
    if lowered in LANGUAGE_SUFFIXES:
        return LANGUAGE_SUFFIXES[lowered]
    if lowered in ("mixed", "typescript/javascript", "ts", "js"):
        return LANGUAGE_SUFFIXES["typescript"] + LANGUAGE_SUFFIXES["javascript"]
    return ()


def _skipped(path: Path, repo: Path) -> bool:
    for part in path.relative_to(repo).parts[:-1]:
        if part in SKIP_DIRS:
            return True
    return False


def surface_files(repo: Path, language: str) -> tuple[str, ...]:
    suffixes = _suffixes(language)
    if not suffixes:
        return ()
    out: list[str] = []
    for candidate in sorted(repo.rglob("*"), key=lambda item: str(item).replace("\\", "/")):
        if candidate.suffix not in suffixes:
            continue
        if _skipped(candidate, repo):
            continue
        try:
            if not candidate.is_file() or candidate.stat().st_size > 400_000:
                continue
            text = candidate.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        lowered = text.lower()
        if any(marker.lower() in lowered for marker in IDENTIFIERS):
            out.append(normalise_path(str(candidate.relative_to(repo)).replace("\\", "/")))
    return tuple(out)


def _pick(candidates: tuple[str, ...], carrier: Carrier, ordinal: int) -> str:
    raw = f"{SEED}|{carrier.name}|{carrier.mechanism}|{ordinal}".encode()
    index = int(hashlib.sha256(raw).hexdigest(), 16) % len(candidates)
    return candidates[index]


def plan(repo: Path, family: Family) -> MutationPlan:
    files = surface_files(repo, family.language)
    if not files:
        return MutationPlan(
            mutations=(),
            skipped=(
                f"no first-party {family.language} file carrying a Google Ads identifier "
                "was found; "
                "no mechanism can be seeded on this family",
            ),
        )
    applicable = [
        carrier
        for carrier in CARRIERS
        if family.language.strip().lower() in carrier.languages
        or (
            family.language.strip().lower() in ("mixed", "ts", "js")
            and ("typescript" in carrier.languages or "javascript" in carrier.languages)
        )
    ]
    skipped: list[str] = []
    if not applicable:
        skipped.append(f"the pack declares no version carrier for language {family.language!r}")
    mutations: list[Mutation] = []
    for ordinal, carrier in enumerate(applicable):
        target = _pick(files, carrier, ordinal)
        text = carrier.template.format(version=SEEDED_VERSION, VERSION=SEEDED_VERSION.upper())
        mutations.append(
            Mutation(
                carrier=carrier.name,
                mechanism=carrier.mechanism,
                path=target,
                line=0,
                text=text,
                version=SEEDED_VERSION,
            )
        )
    return MutationPlan(mutations=tuple(mutations), skipped=tuple(skipped))


def _insert(body: str, text: str, suffix: str) -> tuple[str, int]:
    lines = body.splitlines()
    if suffix == ".php":
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].strip() == "?>":
                lines.insert(index, text)
                return ("\n".join(lines) + "\n", index + 1)
    lines.append(text)
    return ("\n".join(lines) + "\n", len(lines))


def apply(repo: Path, mutations: tuple[Mutation, ...]) -> tuple[Mutation, ...]:
    placed: list[Mutation] = []
    for mutation in mutations:
        target = repo / mutation.path
        if not target.is_file():
            continue
        body = target.read_text(encoding="utf-8", errors="replace")
        updated, line = _insert(body, mutation.text, target.suffix)
        target.write_text(updated, encoding="utf-8")
        placed.append(
            Mutation(
                carrier=mutation.carrier,
                mechanism=mutation.mechanism,
                path=mutation.path,
                line=line,
                text=mutation.text,
                version=mutation.version,
            )
        )
    return tuple(placed)


def labels_for(placed: tuple[Mutation, ...]) -> tuple[Label, ...]:
    out: list[Label] = []
    for index, mutation in enumerate(placed):
        out.append(
            Label(
                label_id=f"S3-{index + 1:04d}",
                path=mutation.path,
                line=mutation.line,
                scope="line",
                mechanism=mutation.mechanism,
                verdict="ACTIONABLE",
                kind="seeded",
                sources=("S3",),
                subject="",
                evidence=f"seeded {mutation.carrier} at {mutation.version}: {mutation.text}",
            )
        )
    return tuple(out)
