from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hubbleops.app.registry import LoadedPack
from hubbleops.core.canonical import canonical_bytes, content_id
from hubbleops.core.errors import HubbleOpsError, ToolingMissing
from hubbleops.core.records import as_mapping, as_sequence, as_text, parse_json
from hubbleops.observe.dynamic.loaders import (
    ATTESTATION_FILENAME,
    LoaderPlan,
    attestation_environment,
    attested,
)
from hubbleops.observe.dynamic.loaders import plan as loader_plan
from hubbleops.observe.dynamic.runner import (
    MAX_EVENT_FILE_BYTES,
    DynamicEventInvalid,
    EventBatch,
    EventIssue,
    event_schema_hash,
    events_from_jsonl,
)
from hubbleops.observe.telemetry import AdapterIssue, ProductionTuple
from hubbleops.sandbox import (
    CAPTURE_IMAGE,
    NODE_CAPTURE_IMAGE,
    PHP_CAPTURE_IMAGE,
    DetachedWorktree,
    FixtureService,
    ImageSpec,
    Mount,
    NetworkPolicy,
    ProxySession,
    ResourceLimits,
    RootlessPodman,
    RunResult,
    RunSpec,
    bounded_process,
    command_record,
    command_transcript,
    interception_failures,
)

SENTINEL_MANIFEST_KEYS = frozenset(
    {
        "adapter_sha256",
        "corpus_sha256",
        "events_sha256",
        "issues",
        "mode",
        "output_limits",
        "package_version",
        "schema_sha256",
    }
)
PHP_LOADER = Path(__file__).parent.parent / "observe" / "dynamic" / "php.ini"


class CaptureInvalid(HubbleOpsError):
    pass


@dataclass(frozen=True, slots=True)
class CaptureAttempt:
    manifest: dict[str, Any]
    batch: EventBatch
    artifacts: dict[str, bytes]

    def fingerprint(self) -> str:
        return content_id(self.manifest)


@dataclass(frozen=True, slots=True)
class ProductionInput:
    manifest: dict[str, Any]
    batch: EventBatch | None = None
    observations: tuple[ProductionTuple, ...] = ()
    issues: tuple[AdapterIssue, ...] = ()
    artifacts: dict[str, bytes] = field(default_factory=lambda: dict[str, bytes]())

    def fingerprint(self) -> str:
        return content_id(self.manifest)


@dataclass(slots=True)
class _CaptureJournal:
    state_dir: Path
    request: dict[str, Any]
    engine: RootlessPodman
    git_records: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    worktree: DetachedWorktree | None = None
    proxy: ProxySession | None = None

    def __enter__(self) -> _CaptureJournal:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        error: BaseException | None,
        _traceback: object,
    ) -> None:
        if error is not None:
            directory = self._persist(error)
            if isinstance(error, HubbleOpsError):
                error.args = (f"{error}; failure artifacts: {directory}",)

    def git_transcript(self) -> bytes:
        worktree = b"" if self.worktree is None else self.worktree.transcript()
        return command_transcript(self.git_records) + worktree

    def proxy_transcript(self) -> bytes:
        return b"" if self.proxy is None else self.proxy.transcript()

    def _persist(self, error: BaseException) -> Path:
        git_commands = self.git_transcript()
        engine_commands = self.engine.transcript()
        proxy_commands = self.proxy_transcript()
        error_bytes = str(error).encode("utf-8", errors="replace")
        manifest = {
            "engine_commands_sha256": _hash(engine_commands),
            "error": error_bytes[:65_536].decode("utf-8", errors="replace"),
            "error_sha256": _hash(error_bytes),
            "error_size": len(error_bytes),
            "error_truncated": len(error_bytes) > 65_536,
            "error_type": type(error).__name__,
            "git_commands_sha256": _hash(git_commands),
            "proxy_commands_sha256": _hash(proxy_commands),
            "request": self.request,
        }
        manifest_bytes = canonical_bytes(manifest) + b"\n"
        directory = self.state_dir / "failures" / content_id(manifest)
        if directory.exists():
            return directory
        directory.mkdir(parents=True, exist_ok=False)
        for name, data in (
            ("engine-commands.jsonl", engine_commands),
            ("failure.json", manifest_bytes),
            ("git-commands.jsonl", git_commands),
            ("proxy-commands.jsonl", proxy_commands),
        ):
            _exclusive_write(directory / name, data)
        return directory


def execute(
    repository: Path,
    repo_sha: str | None,
    pack: LoadedPack,
    command: str,
    language: str,
    mode: str,
    allowlist: tuple[str, ...],
    state_dir: Path,
    *,
    engine: RootlessPodman | None = None,
    limits: ResourceLimits | None = None,
    fixture: FixtureService | None = None,
) -> CaptureAttempt:
    if repo_sha is None:
        raise CaptureInvalid("capture requires a Git repository with a committed HEAD")
    if mode not in ("hook", "proxy"):
        raise CaptureInvalid("capture mode must be hook or proxy")
    if fixture is not None and mode != "proxy":
        raise CaptureInvalid("the test-only fixture service requires proxy mode")
    selected_engine = engine or RootlessPodman()
    selected_limits = limits or ResourceLimits()
    policy = NetworkPolicy.from_values(allowlist)
    hooks = pack.capture_hooks(language)
    loader = loader_plan(language, hooks.paths) if mode == "hook" else None
    image = _image(language)
    state_root = state_dir.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    journal = _CaptureJournal(
        state_root,
        {
            "allowlist": list(allowlist),
            "command": command,
            "fixture": (
                None
                if fixture is None
                else {
                    "hostname": fixture.hostname,
                    "image": fixture.image.fingerprint(),
                    "port": fixture.port,
                    "script_sha256": _hash(fixture.script.read_bytes()),
                }
            ),
            "image": image.fingerprint(),
            "language": language.casefold(),
            "limits": selected_limits.fingerprint(),
            "mode": mode,
            "provider": pack.name,
            "repo_sha": repo_sha,
            "repository": str(repository.resolve()),
        },
        selected_engine,
    )
    with journal:
        return _execute_attempt(
            repository,
            repo_sha,
            pack,
            command,
            language,
            mode,
            policy,
            state_root,
            selected_engine,
            selected_limits,
            fixture,
            loader,
            image,
            journal,
        )


def _execute_attempt(
    repository: Path,
    repo_sha: str,
    pack: LoadedPack,
    command: str,
    language: str,
    mode: str,
    policy: NetworkPolicy,
    state_root: Path,
    selected_engine: RootlessPodman,
    selected_limits: ResourceLimits,
    fixture: FixtureService | None,
    loader: LoaderPlan | None,
    image: ImageSpec,
    journal: _CaptureJournal,
) -> CaptureAttempt:
    _require_clean(repository, journal.git_records)
    with tempfile.TemporaryDirectory(prefix="hops-attempt-", dir=state_root) as raw:
        attempt = Path(raw).resolve()
        worktree_path = attempt / "worktree"
        output = attempt / "output"
        output.mkdir()
        loader_dir = attempt / "loader"
        loader_dir.mkdir()
        nonce = content_id({"attempt": attempt.name, "repo_sha": repo_sha, "command": command})
        installed: bool | None = None
        proxy_commands = b""
        worktree_manager = DetachedWorktree(repository, worktree_path, repo_sha)
        journal.worktree = worktree_manager
        with worktree_manager as worktree:
            if mode == "hook":
                result, proxy_identity, execution_spec = _run_hook(
                    selected_engine,
                    image,
                    worktree,
                    output,
                    pack,
                    command,
                    loader,
                    selected_limits,
                    nonce,
                    loader_dir,
                )
                raw_events = _read_bounded(output / "events.jsonl")
                batch = events_from_jsonl(raw_events)
                installed = attested(_read_bounded(output / ATTESTATION_FILENAME), nonce)
                proxy_log = b""
            else:
                (
                    result,
                    proxy_identity,
                    raw_events,
                    proxy_log,
                    proxy_commands,
                    execution_spec,
                ) = _run_proxy(
                    selected_engine,
                    image,
                    worktree,
                    output,
                    pack,
                    command,
                    policy,
                    selected_limits,
                    attempt.name,
                    fixture,
                    journal,
                )
                batch = _proxy_events(raw_events, pack)
        issues = list(batch.issues)
        if result.outcome != "COMPLETED":
            issues.append(
                EventIssue(
                    "CAPTURE_EXECUTION_FAILED",
                    0,
                    f"workload outcome {result.outcome} with exit code {result.exit_code}",
                )
            )
        blind = interception_failures(proxy_log)
        if blind:
            issues.append(
                EventIssue(
                    "PROXY_INTERCEPTION_FAILED",
                    0,
                    f"{blind} workload connection(s) were never decoded, so any provider request "
                    "they carried was not observed",
                )
            )
        if installed is False:
            issues.append(
                EventIssue(
                    "HOOK_NOT_INSTALLED",
                    0,
                    "the capture hook never ran, so this execution observed nothing at all",
                )
            )
        elif not batch.events and not blind:
            issues.append(EventIssue("UNKNOWN_DYNAMIC", 0, "test execution emitted no events"))
            if mode == "proxy":
                issues.append(
                    EventIssue(
                        "PROXY_BYPASS_BLOCKED",
                        0,
                        "no decodable request reached the only permitted egress proxy",
                    )
                )
        batch = EventBatch(batch.events, tuple(issues))
        command_log = result.log_path.read_bytes()
        engine_identity = selected_engine.identity()
        engine_commands = selected_engine.transcript()
        git_commands = journal.git_transcript()
        manifest = {
            "allowlist": [item.mapping() for item in policy.destinations],
            "command": command,
            "command_log_sha256": _hash(command_log),
            "engine": engine_identity,
            "engine_commands_sha256": _hash(engine_commands),
            "event_schema_sha256": event_schema_hash(),
            "events_sha256": _hash(batch.bytes()),
            "execution_spec": execution_spec,
            "git_commands_sha256": _hash(git_commands),
            "hook": _loader_mapping(loader),
            "hook_installed": installed,
            "image": image.fingerprint(),
            "language": language.casefold(),
            "limits": selected_limits.fingerprint(),
            "mode": mode,
            "network": policy.fingerprint(),
            "outcome": result.outcome,
            "proxy": proxy_identity,
            "proxy_log_sha256": _hash(proxy_log),
            "proxy_commands_sha256": _hash(proxy_commands),
            "raw_events_sha256": _hash(raw_events),
            "repo_sha": repo_sha,
            "stderr_sha256": _hash(result.stderr),
            "stdout_sha256": _hash(result.stdout),
            "workdir": "/workspace",
            "worktree": worktree_manager.attestation(),
            "wire": _wire_mapping(pack),
            "issues": [
                {"code": issue.code, "reason": issue.reason, "row": issue.row}
                for issue in batch.issues
            ],
        }
        artifacts = {
            "command.json": command_log,
            ATTESTATION_FILENAME: _read_bounded(output / ATTESTATION_FILENAME),
            "events.jsonl": batch.bytes(),
            "engine-commands.jsonl": engine_commands,
            "git-commands.jsonl": git_commands,
            "proxy.log": proxy_log,
            "proxy-commands.jsonl": proxy_commands,
            "raw-events.jsonl": raw_events,
            "stderr.log": result.stderr,
            "stdout.log": result.stdout,
        }
        return CaptureAttempt(manifest, batch, artifacts)


def telemetry_input(path: Path, pack: LoadedPack) -> ProductionInput:
    data = _bounded_input(path, MAX_EVENT_FILE_BYTES, "telemetry input")
    parsed = pack.telemetry.parse(data.decode("utf-8"))
    observations = tuple(
        ProductionTuple(item.service, item.method, item.version) for item in parsed.observations
    )
    issues = tuple(AdapterIssue(item.row, item.value, item.reason) for item in parsed.issues)
    if not observations and not issues:
        issues = (AdapterIssue(0, "", "TELEMETRY_EMPTY: export contains no observations"),)
    adapter_path = Path(inspect.getfile(type(pack.telemetry))).resolve()
    manifest = {
        "adapter": type(pack.telemetry).__module__,
        "adapter_identity": content_id(type(pack.telemetry).__qualname__),
        "input_kind": "telemetry",
        "input_sha256": _hash(data),
        "parser_limit": MAX_EVENT_FILE_BYTES,
        "provider_contract_hash": pack.contract_hash(),
    }
    if adapter_path.is_file():
        manifest["adapter_sha256"] = _hash(adapter_path.read_bytes())
    return ProductionInput(
        manifest,
        observations=observations,
        issues=issues,
        artifacts={"telemetry-input": data},
    )


def sentinel_input(events_path: Path, manifest_path: Path, pack: LoadedPack) -> ProductionInput:
    events_data = _bounded_input(events_path, MAX_EVENT_FILE_BYTES, "sentinel events")
    sidecar_data = _bounded_input(manifest_path, 1_048_576, "sentinel manifest")
    parsed = parse_json(sidecar_data)
    if not parsed.ok():
        raise CaptureInvalid(f"sentinel manifest is invalid JSON: {parsed.reason}")
    sidecar = as_mapping(parsed.value)
    if frozenset(sidecar.keys()) != SENTINEL_MANIFEST_KEYS:
        raise CaptureInvalid("sentinel manifest fields do not match the supported contract")
    mode = as_text(sidecar.get("mode"))
    version = as_text(sidecar.get("package_version"))
    if mode not in ("hook", "proxy") or version is None:
        raise CaptureInvalid("sentinel manifest mode or package version is invalid")
    if sidecar.get("events_sha256") != _hash(events_data):
        raise CaptureInvalid("sentinel event hash does not match its manifest")
    corpus = pack.root / "capture" / "wire_conformance.json"
    contract_path = pack.root / "capture" / "sentinel_contract.json"
    try:
        contract = as_mapping(json.loads(contract_path.read_bytes()))
        versions = as_mapping(contract.get("versions"))
        supported = as_mapping(as_mapping(versions.get(version)).get(mode))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CaptureInvalid(f"sentinel compatibility contract is invalid: {error}") from error
    producer = {
        "adapter_sha256": sidecar.get("adapter_sha256"),
        "corpus_sha256": sidecar.get("corpus_sha256"),
        "schema_sha256": sidecar.get("schema_sha256"),
    }
    installed = {
        "corpus_sha256": _hash(corpus.read_bytes()),
        "schema_sha256": event_schema_hash(),
    }
    if (
        not supported
        or dict(supported) != producer
        or supported.get("corpus_sha256") != installed["corpus_sha256"]
        or supported.get("schema_sha256") != installed["schema_sha256"]
    ):
        raise CaptureInvalid("sentinel package is not byte-compatible with the selected pack")
    if sidecar.get("output_limits") != {
        "events": 10_000,
        "input_bytes": MAX_EVENT_FILE_BYTES,
    }:
        raise CaptureInvalid("sentinel output limits do not match the supported contract")
    batch = events_from_jsonl(events_data)
    if any(issue.code != "STACK_TRUNCATED" for issue in batch.issues):
        raise CaptureInvalid("sentinel event file contains invalid records")
    if any(event.get("mode") != mode for event in batch.events):
        raise CaptureInvalid("sentinel event mode disagrees with its manifest")
    sidecar_issues = _sentinel_issues(sidecar.get("issues"))
    combined_issues = {
        (issue.code, issue.row, issue.reason): issue for issue in (*batch.issues, *sidecar_issues)
    }
    sidecar_issues = tuple(
        combined_issues[key] for key in sorted(combined_issues, key=lambda item: (item[1], item[0]))
    )
    if not batch.events and not sidecar_issues:
        sidecar_issues = (
            EventIssue("SENTINEL_EMPTY", 0, "sentinel output contains no observations"),
        )
    batch = EventBatch(batch.events, sidecar_issues)
    manifest = {
        "contract_sha256": _hash(contract_path.read_bytes()),
        "events_sha256": _hash(events_data),
        "input_kind": "sentinel",
        "manifest_sha256": _hash(sidecar_data),
        "mode": mode,
        "output_limits": sidecar["output_limits"],
        "package_version": version,
        "parser_limit": MAX_EVENT_FILE_BYTES,
        "sidecar_limit": 1_048_576,
        **producer,
    }
    return ProductionInput(
        manifest,
        batch=batch,
        artifacts={"sentinel-events": events_data, "sentinel-manifest": sidecar_data},
    )


def _run_hook(
    engine: RootlessPodman,
    image: ImageSpec,
    worktree: Path,
    output: Path,
    pack: LoadedPack,
    command: str,
    loader: LoaderPlan | None,
    limits: ResourceLimits,
    nonce: str,
    loader_dir: Path,
) -> tuple[RunResult, None, str]:
    if loader is None:
        raise CaptureInvalid("hook capture has no loader plan")
    mounts = [
        Mount(worktree, "/workspace", True),
        Mount(output, "/hops/output", False),
        Mount(loader.hook_paths[0].parent, "/hops/hooks", True),
    ]
    if loader.language == "php":
        loader_dir.joinpath(PHP_LOADER.name).write_bytes(PHP_LOADER.read_bytes())
        mounts.append(Mount(loader_dir, "/hops/loader", True))
    environment = (
        *loader.environment,
        *attestation_environment(nonce),
        ("HUBBLEOPS_EVENT_PATH", "/hops/output/events.jsonl"),
        ("PYTHONDONTWRITEBYTECODE", "1"),
    )
    spec = RunSpec(
        image=image,
        command=command,
        mounts=tuple(mounts),
        allowed_mount_roots=(worktree, output, loader_dir, pack.root),
        limits=limits,
        artifact_dir=output,
        environment=tuple(environment),
    )
    return engine.run(spec), None, spec.fingerprint()


def _run_proxy(
    engine: RootlessPodman,
    image: ImageSpec,
    worktree: Path,
    output: Path,
    pack: LoadedPack,
    command: str,
    policy: NetworkPolicy,
    limits: ResourceLimits,
    nonce: str,
    fixture: FixtureService | None,
    journal: _CaptureJournal,
) -> tuple[RunResult, dict[str, object], bytes, bytes, bytes, str]:
    proxy_dir = output / "proxy"
    session = ProxySession(engine, proxy_dir, policy, nonce, limits, fixture)
    journal.proxy = session
    with session as proxy:
        environment = (
            ("ALL_PROXY", "http://capture-proxy:8080"),
            ("HTTPS_PROXY", "http://capture-proxy:8080"),
            ("HTTP_PROXY", "http://capture-proxy:8080"),
            ("NODE_EXTRA_CA_CERTS", "/hops/ca/mitmproxy-ca-cert.pem"),
            ("NO_PROXY", ""),
            ("REQUESTS_CA_BUNDLE", "/hops/ca/mitmproxy-ca-cert.pem"),
            ("SSL_CERT_FILE", "/hops/ca/mitmproxy-ca-cert.pem"),
        )
        spec = RunSpec(
            image=image,
            command=command,
            mounts=(
                Mount(worktree, "/workspace", True),
                Mount(proxy.certificate.parent, "/hops/ca", True),
            ),
            allowed_mount_roots=(worktree, output, pack.root),
            limits=limits,
            artifact_dir=output,
            network=proxy.internal_network,
            environment=environment,
        )
        result = engine.run(spec)
        proxy_log = session.logs(limits.output_bytes)
        flow_path = proxy_dir / "output" / "flows.jsonl"
        flows = _read_bounded(flow_path)
        identity = proxy.mapping()
        execution_spec = spec.fingerprint()
    proxy_commands = session.transcript()
    return result, identity, flows, proxy_log, proxy_commands, execution_spec


def _proxy_events(data: bytes, pack: LoadedPack) -> EventBatch:
    if len(data) > MAX_EVENT_FILE_BYTES:
        issue = EventIssue("EVENT_FILE_TOO_LARGE", 0, "proxy output exceeds bound")
        return EventBatch((), (issue,))
    events: list[dict[str, Any]] = []
    issues: list[EventIssue] = []
    for row, line in enumerate(data.split(b"\n"), start=1):
        if not line.strip():
            continue
        parsed = parse_json(line)
        if not parsed.ok():
            issues.append(EventIssue("EVENT_INVALID", row, str(parsed.reason)))
            continue
        try:
            record = as_mapping(parsed.value)
            path = as_text(record.get("path"))
            if path is None:
                raise DynamicEventInvalid("proxy record has no request path")
            headers = {
                str(key): str(value) for key, value in as_mapping(record.get("headers")).items()
            }
            matched = pack.wire_signature.parse(path, headers)
            if matched.code != "MATCH" or matched.observation is None:
                issues.append(EventIssue("UNKNOWN_WIRE_SIGNATURE", row, matched.reason))
                continue
            if record.get("body_reason") is not None:
                issues.append(
                    EventIssue(str(record["body_reason"]), row, "proxy body not captured")
                )
            events.append(
                {
                    "method": matched.observation.method,
                    "mode": "proxy",
                    "request_text": record.get("request_text"),
                    "request_type": str(record.get("request_type", "http"))[:64],
                    "service": matched.observation.service,
                    "stack": list(as_sequence(record.get("stack"))),
                    "ts": record["ts"],
                    "version": matched.observation.version,
                }
            )
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            KeyError,
            DynamicEventInvalid,
            TypeError,
        ) as error:
            issues.append(EventIssue("PROXY_RECORD_INVALID", row, str(error)))
    normalized = events_from_jsonl(b"".join(canonical_bytes(item) + b"\n" for item in events))
    return EventBatch(normalized.events, (*issues, *normalized.issues))


def _sentinel_issues(value: object) -> tuple[EventIssue, ...]:
    issues: list[EventIssue] = []
    for raw in as_sequence(value):
        issue = as_mapping(raw)
        code = as_text(issue.get("code"))
        row = issue.get("row")
        if code is None or not isinstance(row, int) or isinstance(row, bool) or row < 0:
            raise CaptureInvalid("sentinel issue metadata is invalid")
        issues.append(EventIssue(code, row, str(issue.get("reason", "sentinel input issue"))))
    return tuple(issues)


def _loader_mapping(loader: LoaderPlan | None) -> dict[str, Any] | None:
    if loader is None:
        return None
    return {"fingerprint": loader.fingerprint(), "language": loader.language}


def _wire_mapping(pack: LoadedPack) -> dict[str, str]:
    adapter = Path(inspect.getfile(type(pack.wire_signature))).resolve()
    corpus = pack.root / "capture" / "wire_conformance.json"
    return {
        "adapter": type(pack.wire_signature).__module__,
        "adapter_sha256": _hash(adapter.read_bytes()),
        "corpus_sha256": _hash(corpus.read_bytes()),
    }


def _image(language: str) -> ImageSpec:
    normalized = language.casefold()
    if normalized == "python":
        return CAPTURE_IMAGE
    if normalized in ("javascript", "typescript", "node"):
        return NODE_CAPTURE_IMAGE
    if normalized == "php":
        return PHP_CAPTURE_IMAGE
    raise CaptureInvalid(f"capture language is unsupported: {language}")


def _read_bounded(path: Path) -> bytes:
    if not path.is_file():
        return b""
    with path.open("rb") as handle:
        return handle.read(MAX_EVENT_FILE_BYTES + 1)


def _bounded_input(path: Path, maximum: int, label: str) -> bytes:
    with path.resolve().open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise CaptureInvalid(f"{label} exceeds byte bound")
    return data


def _require_clean(repository: Path, records: list[dict[str, Any]]) -> None:
    argv = ("git", "-C", str(repository.resolve()), "status", "--porcelain=v1")
    started = time.monotonic()
    try:
        outcome, exit_code, stdout, stderr = bounded_process(argv, 30, 65_536, "git")
    except ToolingMissing as error:
        records.append(
            command_record(
                argv,
                time.monotonic() - started,
                None,
                b"",
                str(error),
                "TOOL_MISSING",
            )
        )
        raise CaptureInvalid(f"capture could not verify repository state: {error}") from error
    duration = time.monotonic() - started
    records.append(command_record(argv, duration, exit_code, stdout, stderr, outcome))
    if outcome == "WALL_TIMEOUT":
        raise CaptureInvalid("capture repository status exceeded its 30s wall-time bound")
    if outcome == "OUTPUT_LIMIT":
        raise CaptureInvalid("capture repository status exceeded its 65536-byte output bound")
    if exit_code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        detail = detail or "capture repository is not readable by Git"
        raise CaptureInvalid(detail)
    if stdout:
        raise CaptureInvalid(
            "capture requires a clean repository so execution and ProofScope agree"
        )


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exclusive_write(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise CaptureInvalid(f"capture failure artifact already exists: {path}") from error


__all__ = [
    "CaptureAttempt",
    "CaptureInvalid",
    "ProductionInput",
    "execute",
    "sentinel_input",
    "telemetry_input",
]
