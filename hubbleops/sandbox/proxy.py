from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from hubbleops.core.canonical import canonical_bytes, content_id
from hubbleops.core.errors import ToolingFailed, ToolingMissing
from hubbleops.sandbox.image import PROXY_IMAGE
from hubbleops.sandbox.limits import ResourceLimits
from hubbleops.sandbox.mounts import wsl_path
from hubbleops.sandbox.network import NetworkPolicy
from hubbleops.sandbox.runner import RootlessPodman

ADDON = Path(__file__).with_name("proxy_addon.py")
ENTRYPOINT = Path(__file__).with_name("proxy_entrypoint.py")
LISTENING = b"proxy listening at"
HANDSHAKE_FAILED = b"TLS handshake failed"
PEM_FOOTER = "-----END CERTIFICATE-----"


def interception_failures(log: bytes) -> int:
    return log.count(HANDSHAKE_FAILED)


@dataclass(frozen=True, slots=True)
class ProxyIdentity:
    internal_network: str
    egress_network: str
    container: str
    certificate: Path
    certificate_sha256: str
    implementation_sha256: str
    limits_sha256: str

    def mapping(self) -> dict[str, str]:
        return {
            "certificate_sha256": self.certificate_sha256,
            "image": PROXY_IMAGE.fingerprint(),
            "implementation_sha256": self.implementation_sha256,
            "limits_sha256": self.limits_sha256,
        }

    def fingerprint(self) -> str:
        return content_id(self.mapping())


class ProxySession:
    def __init__(
        self,
        engine: RootlessPodman,
        artifact_dir: Path,
        policy: NetworkPolicy,
        nonce: str,
        limits: ResourceLimits,
    ) -> None:
        self.engine = engine
        self.artifact_dir = artifact_dir.resolve()
        self.policy = policy
        self.limits = limits
        suffix = content_id(nonce)[:12]
        self.internal = f"hops-{suffix}-internal"
        self.egress = f"hops-{suffix}-egress"
        self.container = f"hops-{suffix}-proxy"
        self._created: list[tuple[str, str]] = []

    def __enter__(self) -> ProxyIdentity:
        self.engine.identity()
        self.artifact_dir.mkdir(parents=True, exist_ok=False)
        ca = self.artifact_dir / "ca"
        output = self.artifact_dir / "output"
        trust = self.artifact_dir / "trust"
        ca.mkdir()
        output.mkdir()
        trust.mkdir()
        try:
            self._run("network", "create", "--internal", self.internal)
            self._created.append(("network", self.internal))
            self._run("network", "create", self.egress)
            self._created.append(("network", self.egress))
            arguments = self.start_arguments(ca, output)
            self._run(*arguments)
            self._created.append(("container", self.container))
            self._run("network", "connect", self.egress, self.container)
            inside_certificate = "/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem"
            deadline = time.monotonic() + 15
            certificate_data = ""
            while not certificate_data and time.monotonic() < deadline:
                found = self._run("exec", self.container, "cat", inside_certificate, check=False)
                if found.returncode == 0:
                    certificate_data = found.stdout
                    break
                time.sleep(0.05)
            if not certificate_data:
                logs = self.logs(16_384).decode("utf-8", errors="replace").strip()
                state = self._run(
                    "inspect", "--format", "{{json .State}}", self.container, check=False
                ).stdout.strip()
                files = self._run(
                    "exec",
                    self.container,
                    "find",
                    "/home",
                    "-maxdepth",
                    "4",
                    "-type",
                    "f",
                    "-print",
                    check=False,
                ).stdout.strip()
                detail = "proxy CA certificate was not generated"
                raise ToolingFailed(
                    "mitmproxy", f"{detail}: state={state}; files={files}; logs={logs}"
                )
            if not certificate_data.rstrip().endswith(PEM_FOOTER):
                raise ToolingFailed(
                    "mitmproxy", "the disposable CA was read before it was complete"
                )
            payload = certificate_data.encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            certificate = trust / "mitmproxy-ca-cert.pem"
            with certificate.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if hashlib.sha256(certificate.read_bytes()).hexdigest() != digest:
                raise ToolingFailed(
                    "mitmproxy", "the disposable CA did not read back as the bytes just written"
                )
            limits_path = output / "limits.json"
            if not limits_path.is_file():
                raise ToolingFailed("mitmproxy", "proxy resource-limit attestation is missing")
            limits_data = limits_path.read_bytes()
            if limits_data != canonical_bytes(self._expected_limits()) + b"\n":
                raise ToolingFailed("mitmproxy", "proxy resource limits do not match the request")
            self._await_listener()
            return ProxyIdentity(
                self.internal,
                self.egress,
                self.container,
                certificate,
                digest,
                content_id(
                    {
                        "addon": hashlib.sha256(ADDON.read_bytes()).hexdigest(),
                        "entrypoint": hashlib.sha256(ENTRYPOINT.read_bytes()).hexdigest(),
                    }
                ),
                hashlib.sha256(limits_data).hexdigest(),
            )
        except BaseException:
            self._cleanup()
            raise

    def start_arguments(self, ca: Path, output: Path) -> tuple[str, ...]:
        allowlist = json.dumps(
            [item.mapping() for item in self.policy.destinations],
            sort_keys=True,
            separators=(",", ":"),
        )
        limits = self.limits
        return (
            "run",
            "--detach",
            "--pull=never",
            "--name",
            self.container,
            "--user",
            PROXY_IMAGE.user,
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            "--network",
            self.internal,
            "--network-alias",
            "capture-proxy",
            "--read-only",
            "--no-hosts",
            "--pids-limit",
            str(limits.processes),
            "--memory",
            str(limits.memory_bytes),
            "--log-driver",
            "k8s-file",
            "--log-opt",
            f"max-size={limits.output_bytes}",
            "--env",
            "HOME=/home/mitmproxy",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--volume",
            f"{wsl_path(ADDON)}:/hops/proxy/addon.py:ro,nosuid,nodev",
            "--volume",
            f"{wsl_path(ENTRYPOINT)}:/hops/proxy/entrypoint.py:ro,nosuid,nodev",
            "--volume",
            f"{wsl_path(ca)}:/home/mitmproxy:rw,nosuid,nodev",
            "--volume",
            f"{wsl_path(output)}:/hops/output:rw,nosuid,nodev",
            "--entrypoint",
            str(PROXY_IMAGE.entrypoint),
            PROXY_IMAGE.reference,
            "/hops/proxy/entrypoint.py",
            str(limits.cpu_seconds),
            str(limits.memory_bytes),
            str(limits.address_space_bytes),
            str(limits.processes),
            str(limits.open_files),
            str(limits.file_bytes),
            "--",
            "/usr/local/bin/mitmdump",
            "--listen-host",
            "0.0.0.0",
            "--listen-port",
            "8080",
            "--set",
            "hops_output=/hops/output/flows.jsonl",
            "--set",
            f"hops_allowlist={allowlist}",
            "--set",
            "connection_strategy=lazy",
            "--scripts",
            "/hops/proxy/addon.py",
        )

    def _expected_limits(self) -> dict[str, list[int]]:
        limits = self.limits
        return {
            "address_space": [limits.address_space_bytes, limits.address_space_bytes],
            "cpu": [limits.cpu_seconds, limits.cpu_seconds],
            "data": [limits.memory_bytes, limits.memory_bytes],
            "file_size": [limits.file_bytes, limits.file_bytes],
            "open_files": [limits.open_files, limits.open_files],
            "processes": [limits.processes, limits.processes],
        }

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._cleanup()

    def logs(self, maximum: int) -> bytes:
        completed = self._run("logs", self.container, check=False)
        return (completed.stdout + completed.stderr).encode("utf-8")[:maximum]

    def _await_listener(self) -> None:
        deadline = time.monotonic() + 30
        seen = b""
        while time.monotonic() < deadline:
            seen = self.logs(65_536)
            if LISTENING in seen:
                return
            time.sleep(0.05)
        detail = seen.decode("utf-8", errors="replace").strip()
        raise ToolingFailed(
            "mitmproxy",
            f"proxy never began listening, so no capture could observe egress: {detail}",
        )

    def _cleanup(self) -> None:
        for kind, name in reversed(self._created):
            arguments = ("rm", "--force", name) if kind == "container" else ("network", "rm", name)
            self._run(*arguments, check=False)
        self._created.clear()

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        argv = (*self.engine.prefix, *arguments)
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=_host_environment(),
            )
        except FileNotFoundError as error:
            raise ToolingMissing("podman", f"{argv[0]!r} is not executable") from error
        except subprocess.TimeoutExpired as error:
            raise ToolingFailed("podman", "proxy lifecycle command timed out") from error
        if check and completed.returncode != 0:
            raise ToolingFailed("podman", completed.stderr.strip() or "proxy command failed")
        return completed


def _host_environment() -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


__all__ = ["ProxyIdentity", "ProxySession", "interception_failures"]
