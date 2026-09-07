# Phase 4 plan — Dynamic capture, isolated execution, telemetry, and sentinel

## Scope and maturity

Classification: **Build**, Phase 4, targeting local tag `v0.4`. Phase 3 is merged on `main`, tagged
`v0.3`, and has a literal post-loop `GATE: PASS`. Ubuntu WSL now has Podman 4.9.3 and the selected
engine has been measured as Linux, local, rootless, uid/gid-remapped, seccomp-enabled, and able to
run a capability-free non-root container with a read-only root and no network. The host's Docker
Desktop engine is rootful and is therefore not an eligible Phase 4 runner.

This phase stops after isolated test-time capture, telemetry reconciliation, the standalone
sentinel, promotion, a fresh gate, and the Phase 4 real-repo loop. It does not implement verification,
repair, obligations, receipts, deployment, production credentials, or P-009 producer attestation.
AI triage remains default-off and disconnected.

## Outcome

A successful implementation lets a user run:

```text
hops capture <repo> --pack <name> --cmd "<test command>"
```

and receive an evidence-backed ledger from the repository's own tests without exposing host
credentials or granting unrestricted network access. Proxy mode is the default and hook mode is an
explicit alternative. Both produce the same versioned event format. Provider telemetry and
standalone sentinel output reconcile to candidates without silently proving absence. A confirmed
dynamic or sentinel wrapper can be promoted into a revocable repository-local ast-grep rule that
the next static scan consumes.

## Why this matters

Phase 3 can prove many wrapper paths statically but correctly preserves runtime configuration,
opaque call paths, and query holes as UNKNOWN. Phase 4 adds independent execution, wire, and
production-observation channels. The value is reduced uncertainty with provenance, not a promise
that one passing test run observed all production behavior.

The highest-risk assumption is that customer tests can run in an isolated container with either a
pack hook or an egress proxy in their path. The cheapest credible proof is the existing DI wrapper
fixture executed in both modes, with equivalent provider tuple identity and a recorded hook stack,
plus direct adversarial probes for network bypass, environment leakage, timeout, mounts, and limits.

## Success measures

- The DI fixture yields the same `(service, method, version)` candidate identity in hook and proxy
  modes; hook evidence carries a complete repository stack and proxy evidence carries the matched
  request target.
- A host-only sentinel environment variable is absent inside the workload container.
- Direct application-container egress fails; proxy egress rejects a destination outside the
  allowlist; the empty allowlist permits no forwarding.
- Workload and proxy containers run as non-root numeric uids with all Linux capabilities dropped,
  `no-new-privileges`, read-only root filesystems, and bounded CPU time, address space, processes,
  open files, output, and wall time. Mandatory in-container POSIX limits remain active when a
  rootless host cannot delegate cgroup controllers; ignored runtime-limit warnings are recorded and
  cannot be presented as enforcement.
- Every subprocess invocation, duration, exit code, stdout, and stderr is recorded under the run's
  ignored artifact directory without recording inherited host environment values.
- Zero dynamic events with static call sites yields one or more `UNKNOWN_DYNAMIC` candidates with a
  precise closing instruction. It never yields evidence of no usage.
- A deliberately unmatched telemetry tuple yields `TELEMETRY_UNEXPLAINED`; a tuple with at least
  one explained exact mapping counts once in deterministic `Production services accounted for N/M`
  output, while multiple source-site mappings retain explicit association ambiguity.
- The sentinel wheel installs in an isolated environment and its hook and proxy commands emit events
  validated against byte-identical copies of the shared schema without importing `hubbleops.*`.
- Promotion is idempotent and revocable, records run/evidence/source provenance, and reduces the
  confirmed fixture wrapper to a direct promoted sink on the next scan.
- Full tests, property tests, Ruff, formatting, strict Pyright, import/provider-leak checks, package
  build/install checks, rootless Podman integration probes, deterministic artifact checks, and diff checks
  pass. The post-loop fresh auditor returns literal `GATE: PASS`.

## Relevant contracts and existing system

- `CLAUDE.md` Laws L1, L3, L4, L5, L7, L9, and L10 remain controlling.
- Frozen `Evidence`, `Candidate`, `ProofScope`, and `Observer` schemas/interfaces remain unchanged.
- `ProviderPack.capture_hooks(language)`, `wire_signature`, and `telemetry` are injected by `app/`;
  generic sandbox and observer modules never import or name a pack.
- `request_text` and `production_version` use the frozen per-claim precedence in §5.
- Phase 3 static observations and wrapper chains are rebuilt under the capture ProofScope rather
  than copied across a scope boundary.
- Capture and repair share `sandbox/`; Phase 5 uses a separate `sandbox/verifier_image.py` contract.
- `.hubbleops/surface.yml` is the frozen repository-local promotion location. Capture artifacts,
  logs, worktrees, images, SQLite files, and events remain uncommitted.
- P-006 requires the language-independent wire channel. P-008 makes the versioned request target
  authoritative over client metadata. P-009 stays open and operational AI remains unreachable.

## Deliverable boundaries

### Sandbox

Add `hubbleops/sandbox/{runner,image,limits,network,mounts,capture,verifier_image}.py` with small value
objects that validate before constructing any process invocation. The application workload runs in
a detached git worktree at the captured SHA. Temporary paths are resolved and checked beneath the
run artifact root before cleanup.

Git worktree creation has one narrow, unavoidable host write outside those paths: Git owns
administrative metadata under the target repository's resolved git-common-dir `/worktrees/`.
Before invoking Git, the runner resolves the repository with `git rev-parse`, requires the metadata
target to be beneath that exact git-common-dir, records the before/after entry, and permits only
`git worktree add --detach` and `git worktree remove` to mutate it. It never edits source files in
the prospect checkout or any other `.git` path.

The runner refuses every engine unless its machine-readable security report says `rootless=true`.
The workload container is non-root, capability-free, no-new-privileges, read-only at its root,
limited, and attached only to a per-run internal network. Mandatory `RLIMIT_CPU`, `RLIMIT_AS`,
`RLIMIT_NPROC`, `RLIMIT_NOFILE`, and `RLIMIT_FSIZE` enforcement complements runtime cgroup flags;
the parent enforces wall time and bounded stdout/stderr. A separate, equally hardened capture-proxy
container may join that internal network and an external bridge. The workload therefore cannot
bypass the proxy.

The proxy is the official mitmproxy 12.2.3 image pinned as
`docker.io/mitmproxy/mitmproxy@sha256:00b77b5d8804c8ad18cb6caefbf9d5849e895e8986c5ce011f4ae30f4385962f`,
invoked through a fixed container-only Python entrypoint that applies and attests the mandatory
rlimits before replacing itself with `mitmdump`, as uid/gid 1000 with the same capability,
filesystem, process, and output bounds as the workload. A generic mounted add-on owns policy and emits neutral flow
records; provider parsing remains injected in `app/`.

The proxy allowlist consists of exact normalized `(scheme, host, port)` entries. An empty allowlist
is valid deny-all configuration and permits zero forwarded destinations. The proxy rejects IP
literals unless explicitly listed, credentials in authorities, loopback, unspecified,
link-local, multicast, and private destinations, post-resolution forbidden addresses, DNS changes
between policy and connection, disallowed redirect targets, oversized or invalid headers, and
oversized or opaque bodies. IPv4 and IPv6 receive the same policy. Logs redact authorization,
cookies, API keys, and configured sensitive fields. Request/body/event limits are hard bounds.
Plain HTTP is decoded. For HTTPS/gRPC, the proxy generates a per-execution disposable CA, exposes
only its public certificate to the workload via a read-only mount and generic runtime trust
variables, decrypts the request in the separately constrained proxy, and records the versioned
target for the injected wire signature. The private key is confined to the proxy's validated
temporary directory and destroyed after artifact finalization; its public-certificate hash and
proxy configuration enter ProofScope. Certificate pinning, unsupported trust stores, failed
handshakes, or an application ignoring proxy settings yields `TLS_INTERCEPTION_FAILED` or
`PROXY_BYPASS_BLOCKED`, never absence. The DI equivalence test uses a TLS/gRPC-shaped target on a
separate fixture service reachable only from the proxy's egress network, proving the normal
language-independent TLS path without provider credentials or production traffic.

The integration runner creates one test-only exception for that service: its generated container
identity, network id, exact hostname, port, and resolved address are bound into the execution
manifest, and the proxy accepts that private address only when all five values match the runner's
per-run fixture record. The public CLI cannot declare this exception. It is absent from customer
capture, expires with the run network, and does not relax unconditional denial of undeclared
private, loopback, link-local, host, or user-supplied destinations.

The capture image is reproducibly described. ProofScope binds hashes of the exact dynamic schema,
generic loader assets, selected pack hook assets, pack wire implementation plus declarative
conformance corpus, normalized allowlist, proxy implementation and resolved image digest, workload
image digest, runner/runtime identity, command/working directory, limits, mounts, capture mode, and
the immutable execution manifest. `verifier_image.py` defines a separately hashed immutable
configuration and is never accepted as a runner mode.

### Shared event contract and dynamic observer

Add `hubbleops/observe/dynamic/schema.json`, schema revision `1`, and generic
validation/normalization, loaders, proxy-event parsing, and runner integration. Event objects use
the architecture fields `{version, service, method, request_text, request_type, stack, ts}` plus the
Phase 4 prompt's optional `mode: hook|proxy`, with `additionalProperties: false`; observer
provenance lives in Evidence and the execution manifest. Version/service/method/type are non-empty bounded
strings, request text is bounded or null, and timestamps are RFC 3339 UTC. A stack retains every
captured repository, dependency, library, and runtime frame in order: repository paths are
slash-normalized and relative, while container-only external paths are normalized beneath
`<dependency>` or `<runtime>` so host paths cannot leak. Frames carry kind, path, positive line when
known, and function. The schema imposes a hard frame bound; exceeding it appends an explicit
truncation frame with the omitted count and emits `STACK_TRUNCATED` UNKNOWN, so a limited trace is
never called full. Canonical
ordering and JSONL encoding are deterministic for a fixed event set. Oversize, non-canonical, or
schema-invalid input is retained raw only as a bounded artifact and yields a named UNKNOWN.

Hook loaders use Python `sitecustomize`, PHP `auto_prepend_file`, and Node `--require` to load only
the paths returned by the selected pack. The loaders contain no provider knowledge. Proxy records
carry request path, normalized headers, request body when bounded and textual, and an explicit
truncation/opaque reason. `app/` applies the injected `wire_signature` exactly once to turn raw wire
records into typed events or named UNKNOWN evidence.

Dynamic event conversion emits:

- `production_version` evidence keyed by service, method, and version for hook/proxy equivalence;
- same-identity `call_version` and `request_text` evidence when a repository stack location matches
  a static candidate, allowing L3-compliant closure through newly attached evidence;
- an AFFECTED candidate with reason `OBSERVED_NOT_STATIC` when execution proves a call absent from
  the static candidate set;
- `UNKNOWN_DYNAMIC` when static call sites exist but the test run emits no events;
- named UNKNOWN evidence for malformed, truncated, opaque, or unmatched proxy records.

Each invocation first writes append-only partial JSONL and bounded logs to a fresh temporary attempt
directory. On success, non-zero exit, timeout, signal, malformed output, or observer loss, the parent
flushes those partial artifacts and finalizes an immutable execution manifest containing their
hashes and the exact exit/failure state. The manifest hash enters `build_config_hash` before ledger
materialization. Its resulting run directory is content-addressed and creation is no-clobber: two
executions can never overwrite or silently union event sets. Fixed manifest bytes produce the same
run id and artifact bytes; distinct outputs, timestamps, failure states, or event sets produce
distinct run ids.

No event, hook output, or proxy output is trusted until schema validation, source-path confinement,
source-hash verification, ProofScope rebinding, and deterministic deduplication succeed. Valid
partial events survive a failed or timed-out workload and remain paired with a named execution
UNKNOWN; failure does not discard observations or imply absence.

### Provider capture hooks

Add Google Ads capture assets under `hubbleops/packs/google_ads/capture/{python,php,node}/` and return
them from `capture_hooks(language)`. They may name the provider because they are pack-owned. The
Python hook supplies the gate's executable DI evidence; PHP and Node loaders/hooks receive smoke and
failure tests so this is not a Python-only capture design. Add a minimal `_mock` hook so pack
injection remains testable without generic-layer changes.

Provider hook assets do not import the main `hubbleops` package inside the workload. They emit the
shared event shape to the path supplied by the generic loader and include a bounded full stack.
Malformed hook output fails closed and remains an artifact. A pack-owned declarative wire
conformance corpus covers positive, negative, versioned, malformed, redirect, authority-only, and
bounded-body cases; the independent sentinel vendors the corpus bytes, and the root suite requires
byte identity plus identical normalized outputs across pack and sentinel adapters.

### Telemetry reconciliation and Exposure Map

Add generic `hubbleops/observe/telemetry.py`. It accepts an injected adapter result and an existing
ledger, produces telemetry Evidence, and maps every unique `(service, method, version)` tuple to at
least one explained candidate or a `TELEMETRY_UNEXPLAINED` UNKNOWN. Adapter issues also become named
UNKNOWNs rather than disappearing.

`hops capture <repo> --pack <name> --telemetry-export <file>` and
`--sentinel-events <file> --sentinel-manifest <file>` are explicit application-layer ingestion
paths. Before parsing either,
the application finalizes a no-clobber production-input manifest containing the exact input-byte
hash, input kind, import mode, schema hash, selected telemetry/wire adapter bytes and identity,
mandatory sentinel package version, conformance-corpus hash, repository/tree/dependency hashes,
and parser limits. The manifest hash enters `build_config_hash`; its run and raw bounded artifact
are content-addressed before Evidence materialization. Separate imports never overwrite or union
under one provenance claim. Malformed, foreign-schema, or adapter-mismatched input yields a scoped
UNKNOWN and retained artifact rather than partial trust. A sentinel import requires every event's
`mode` to equal the manifest mode; the CLI never accepts a caller-supplied mode override.

Each pack ships a provider-owned `capture/sentinel_contract.json` mapping supported sentinel
package versions and modes to the expected adapter-source, schema, and conformance-corpus hashes;
its exact bytes join `provider_contract_hash`. Ingestion recomputes the event-file hash, validates
each event against the current dynamic schema, hashes the current schema/corpus, and compares every
sidecar field against that pack-owned expected record. It rejects the entire import before Evidence
materialization if the version is unsupported, any hash differs, modes disagree, fields are absent,
or the file exceeds its declared bound. This validates integrity/compatibility, not producer
identity or authenticity, and sentinel evidence remains non-closing on its own.

`production_version` candidate identity includes service, method, and version. Resolver behavior is
observer-specific: unmatched telemetry remains UNKNOWN; dynamic or sentinel execution can be
AFFECTED with `OBSERVED_NOT_STATIC`; matched evidence records the candidate ids it reconciles.
Exposure derives its production denominator from unique telemetry/sentinel tuples and its numerator
from tuples with at least one explained candidate mapping, matching frozen §6.6. Exact matching
means all normalized service, method, and version fields are equal. Zero matches is
`TELEMETRY_UNEXPLAINED` and is not accounted. Multiple legitimate source-site mappings increment the
tuple numerator once but emit `TELEMETRY_SITE_AMBIGUOUS` association evidence; they never attribute
the production call to one site or close any per-site UNKNOWN. With no production observer it
preserves the existing text exactly.

### Standalone sentinel

Add `packages/hubbleops-sentinel/` as a separate distribution with its own source tree, version,
tests, wheel metadata, and CLI. It uses only the Python standard library. It never imports or loads
`hubbleops.*`, never writes a verdict, and never shares runtime code with test capture.

The sentinel has two commands over the same vendored event schema and conformance corpus:

- hook mode installs its own Google Ads logging/interceptor adapter and writes observed events;
- proxy mode, documented and presented as the recommended default, reads bounded egress/client
  request logs and applies its independent wire adapter without any language-specific loader.

Both modes write atomic local JSONL plus a mandatory atomic `<output>.manifest.json` sidecar. The
sidecar contains the sentinel package version, mode, exact adapter-source hash, schema hash,
conformance-corpus hash, event-file hash, and output limits. It contains no wall-clock generation
field: fixed input events, including their observational timestamps, produce byte-identical JSONL
and sidecar bytes. Optional URL export
is explicit, bounded by a timeout, and sends only validated event bytes. Export failure leaves the local artifact intact and exits non-zero. The
root suite asserts that the sentinel schema bytes match the dynamic schema and that no source,
metadata, test, or built wheel imports `hubbleops`. It also executes the corpus against both
independent wire adapters and rejects semantic drift; the sentinel never imports pack code at
runtime.

Sentinel ingestion emits only `production_version` Evidence and promotion-eligible observed stack
provenance; it is mechanically forbidden from emitting or attaching `call_version` or
`request_text` Evidence at a static candidate identity. The resolver and store integration retain a
pre-existing static UNKNOWN when the only new observation is sentinel. Tests inject forged
sentinel-labelled call-site evidence and require rejection. A later scan may independently use a
source-valid promoted rule, but that structure observation—not sentinel alone—performs any closure.

### Promotion

Add `hops promote` with explicit repository, run, candidate, and state inputs. Only a candidate with
dynamic or sentinel observed stack evidence can be promoted. Promotion writes a schema-versioned,
sorted `.hubbleops/surface.yml` entry containing an active/revoked state, language, symbol, a valid
ast-grep rule, run id, evidence ids, and source hash.

Creating or reactivating an entry requires current same-scope OBSERVED stack evidence and matching
source bytes. Revocation addresses an existing provenance-bound entry by its stable identity and is
always allowed after source drift or deletion; it changes only that entry to `revoked` and cannot
create, reactivate, or rewrite its rule. On every scan, `app/` re-hashes the active entry's recorded
source path before materializing its neutral rule. A mismatch or missing source never applies the
rule and aborts with the named `PROMOTION_SOURCE_DRIFT` fail-closed outcome; the user can still run
revocation against the stable entry identity afterward. `hops scan` binds validated active promotion
bytes into `rules_hash` and passes materialized neutral rules to the structure observer. Revocation removes the rule from the
active set without deleting provenance. Duplicate promotion is byte-idempotent. Invalid YAML,
foreign-run evidence, source drift during creation/reactivation, unsupported language, or a missing
stack fails closed.

## Definition of done

1. All six sandbox modules and separate verifier image module exist and enforce the isolation,
   worktree, network, limit, mount, timeout, and logging facts above with unit and real-runtime tests.
2. The versioned dynamic schema validates both modes; Python/PHP/Node generic loaders inject only
   pack assets; proxy is the CLI default and hook is opt-in.
3. `hops capture` persists a capture-scoped ledger and no-clobber content-addressed execution
   manifests/events/log artifacts, including partial evidence from failures and timeouts.
   Hook/proxy DI runs have equivalent provider candidates; hook has the stack; zero events with
   static sites is UNKNOWN; observed-only calls are AFFECTED with `OBSERVED_NOT_STATIC`.
4. Generic telemetry/sentinel import uses no-clobber content-addressed production-input manifests;
   reconciliation accounts for every tuple or emits `TELEMETRY_UNEXPLAINED`, and Exposure renders
   deterministic `N/M` production coverage.
5. The independently packaged sentinel installs and passes local-output plus mandatory producer
   manifest, URL-export failure, hook, proxy, schema, no-verdict, no-UNKNOWN-alone, and no-import tests.
6. `hops promote` is provenance-bound, idempotent, revocable, and consumed by the next scan as an
   active ast-grep rule.
7. Capture uses no production credentials, no host environment leakage, no unrestricted workload
   egress, no host writes outside validated worktree/artifact/repository-promotion targets and the
   exact Git-owned `.git/worktrees` metadata needed for detached worktree lifecycle, and no silent
   fallback.
8. Existing Phase 1–3 behavior and byte determinism remain green; no generic layer imports a pack or
   contains a provider name.
9. Evidence commands in the Phase 4 prompt are executed, a fresh audit returns literal
   `GATE: PASS`, the real-repo loop runs `scan`, `exposure`, and `capture` on two pinned repositories,
   every UNKNOWN receives one allowed disposition, every NEW_PATTERN becomes an anonymized fixture,
   and a final fresh gate passes after any loop change.

## Non-negotiable invariants

- L1/L3/L4/L5/L7/L9/L10 and all frozen schemas/interfaces remain mechanically enforced.
- Test capture and production sentinel share schema bytes, not implementation code or imports.
- Sentinel input without a matching mandatory producer manifest stays UNKNOWN; sentinel evidence
  alone never attaches to or closes a static call-site UNKNOWN.
- Capture never inherits or discovers production credentials. Explicit production-looking secret
  variable names are rejected even if requested.
- Application workload egress is impossible except through the policy proxy; default is deny-all.
- Proxy is the default, not a lower-confidence fallback. Hook and proxy evidence differ only where
  the channel genuinely observes different facts, such as stacks.
- Zero events, malformed events, opaque TLS, adapter issues, runtime loss, timeout, and source drift
  become named UNKNOWN/failure outcomes, never absence or a smaller ledger.
- Dynamic, telemetry, and sentinel evidence bind to the current tree, dependencies, pack contract, exact
  schema/loaders/hooks/wire corpus/proxy bytes, normalized policy, runtime identity, command, images,
  execution manifest, and capture configuration. No evidence crosses a ProofScope.
- Promotion preserves provenance and revocation history. Memory reduces future work and never proves
  a verdict.
- Verifier isolation is a separate image/config and cannot be selected as a runner flag.
- No command pushes, publishes, deploys, uses real provider credentials, or writes to prospect
  repositories during the real-repo loop.

## Authority

Authorized autonomously: inspect the repository and local runtime; use the measured rootless Podman
engine in Ubuntu WSL; create the Phase 4 branch; implement scoped modules, package assets, schemas, tests,
fixtures, and required completion records; build local images and wheels; pull a pinned public base
image when required; create/remove validated temporary worktrees, containers, networks, and volumes;
run non-destructive tests and the public-repository loop; make logical local commits; merge to
`main`; and create local tag `v0.4` only after the literal final gate pass.

Requires human approval: changing a frozen schema/interface or P-009; accepting production
credentials; weakening isolation; persistent writes outside the target repository's explicit
`.hubbleops/surface.yml` promotion or the selected state directory; adding a sentinel runtime
dependency; incurring material paid cost; pushing, publishing, deploying, or releasing.

## Explicitly outside scope

- Verification, Receipt/verdict generation, repair, obligations, change application, or agent access.
- Production deployment or enrollment of the sentinel and live provider credentials.
- Field-level contract validation and Phase 5 request-shape differential checks.
- Transparent capture of protocols a selected proxy cannot decode; these remain explicit UNKNOWNs.
- Java/C# capture hooks, Kubernetes, orchestration, cloud control planes, dashboards, databases, or
  generic plugin frameworks.
- Enabling AI triage or implementing producer attestation while P-009 is open.
- Phase 7 decision/retired/binding registries beyond the single Phase 4 promotion file required now.

## Risks, assumptions, and alternatives

1. **Container limit portability.** The measured WSL rootless Podman host is on hybrid cgroup v1 and
   reports that cgroup resource flags are ignored. The runner therefore requires POSIX rlimits and
   parent wall/output bounds, records the runtime warning, and proves each bound adversarially. A
   runtime with neither delegated cgroups nor working rlimits is rejected.
2. **TLS/protocol opacity.** A proxy cannot claim a provider tuple from an undecodable request.
   The selected pinned mitmproxy container performs controlled interception using a disposable CA
   trusted only by the workload. Failed trust injection, pinning, or missing path visibility remains
   a named UNKNOWN. Never infer version from client metadata or CONNECT authority.
3. **Hook brittleness.** Library internals change. Keep hooks pack-owned, smoke all three loaders,
   and let proxy remain the default cross-language path.
4. **Container escape or credential exposure.** Validate mounts and target paths, pass an allowlisted
   environment from scratch, use an internal workload network, drop privileges/capabilities, and
   test hostile commands.
5. **False telemetry reconciliation.** Match exact normalized tuples and retain unmatched records
   as UNKNOWN. A tuple with multiple legitimate explained source mappings counts once as accounted
   but keeps site association ambiguous and never closes per-site unknowns. Do not fuzzy-match
   provider operations.
6. **Sentinel dependency coupling.** Keep it stdlib-only and audit wheel contents/import AST. A
   shared library was rejected because it violates the independent-products boundary. Byte-identical
   schema/corpus assets and cross-product conformance tests provide mechanical drift detection.
7. **Promotion overreach.** Promote one observed symbol and exact source hash, keep it revocable,
   and prove source drift invalidates it. Auto-promoting inferred/AI evidence was rejected.
8. **Doing nothing.** Leaves runtime-only calls and production usage permanently UNKNOWN and fails
   the ordered Phase 4 contract.

## Verification

Required commands and objective evidence:

```text
wsl.exe -d Ubuntu -- podman info --format json
wsl.exe -d Ubuntu -- sh -ceu 'test "$(podman info --format "{{.Host.Security.Rootless}}")" = true; echo rootless=true'
wsl.exe -d Ubuntu -- podman run --rm --user 65532:65532 --cap-drop=all --security-opt=no-new-privileges --network=none --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m docker.io/library/alpine@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce sh -ceu "ulimit -t 2; ulimit -v 131072; ulimit -u 64; ulimit -n 64; ulimit -f 128; id; test ! -w /"
wsl.exe -d Ubuntu -- podman run --rm --user 1000:1000 --cap-drop=all --security-opt=no-new-privileges --network=none --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m --tmpfs /home/mitmproxy/.mitmproxy:rw,noexec,nosuid,nodev,size=16m --entrypoint /usr/local/bin/mitmdump docker.io/mitmproxy/mitmproxy@sha256:00b77b5d8804c8ad18cb6caefbf9d5849e895e8986c5ce011f4ae30f4385962f --version
uv run pytest -q
uv run pytest -q tests/unit/test_sandbox_*.py tests/unit/test_dynamic*.py tests/unit/test_telemetry.py
uv run pytest -q tests/integration/test_sandbox_runtime.py -k "rootless or cpu_limit or memory_limit or process_limit or file_descriptor_limit or file_size_limit or wall_timeout or output_limit or environment or mount or network"
uv run pytest -q tests/integration/test_capture_*.py tests/integration/test_promotion.py
uv run pytest -q tests/property/test_phase4_*.py
uv run pytest -q tests/unit/test_imports.py tests/unit/test_no_provider_leak.py
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv build --package hubbleops-sentinel --out-dir .hubbleops/artifacts/phase4-sentinel-dist
uv venv --seed --clear .hubbleops/artifacts/phase4-sentinel-venv
uv pip install --python .hubbleops/artifacts/phase4-sentinel-venv/Scripts/python.exe --no-deps .hubbleops/artifacts/phase4-sentinel-dist/hubbleops_sentinel-0.1.0-py3-none-any.whl
.hubbleops/artifacts/phase4-sentinel-venv/Scripts/python.exe -I -m hubbleops_sentinel hook --input tests/fixtures/phase4/sentinel_hook_input.jsonl --output .hubbleops/artifacts/phase4-sentinel-hook.jsonl
.hubbleops/artifacts/phase4-sentinel-venv/Scripts/python.exe -I -m hubbleops_sentinel proxy --input tests/fixtures/phase4/sentinel_proxy_input.jsonl --output .hubbleops/artifacts/phase4-sentinel-proxy.jsonl
git diff --check
```

Manual evidence prints the DI hook stack, the equivalent proxy candidate id, a denied direct egress
attempt, absence of the host-secret canary, a timed-out process, an unmatched telemetry tuple,
`Production services accounted for N/M`, and the promotion before/after hop count. Every command's
exit code is recorded. Determinism compares event normalization, telemetry reconciliation,
promotion YAML, command plans, and artifact bytes for identical fixed inputs.

After the implementation gate, run current `scan`, `exposure`, and `capture` against the two pinned
public repositories already stored under `.hubbleops/artifacts/phase2-real-repos/`. Use
`python -m unittest discover -v` for `mcp-google-ads` and `npm test -- --runInBand` for
`google-ads-api`, in their pinned worktrees, with a deny-all network, an environment built only from
fixed safe variables, and no dependency installation or credentials. A missing offline dependency
or non-zero test result is an explicit `CAPTURE_EXECUTION_FAILED` UNKNOWN with preserved partial
events, never a reason to enable network or invent coverage. Classify every UNKNOWN using the
operating protocol. Any new generalized pattern goes through the fixture-writer and is entered in
the Failure Atlas. Rerun the complete fresh gate on the post-loop tree unconditionally.

## Release and learning

This phase creates local CLI/package capability only. There is no production rollout or sentinel
deployment. Local rollback is the Phase 4 merge revert while `v0.3` remains intact. Capture artifacts
are disposable and content-addressed; repository promotion is recoverable from git and revocable in
place.

The real-repo loop measures event yield, UNKNOWN_DYNAMIC rate, proxy opacity, hook failures,
telemetry reconciliation, and promotion usefulness. A pattern graduates only when generalized and
anonymized. Phase 5 proceeds only after the final post-loop gate passes.

## Architecture record

No frozen architecture change is planned. The chosen two-container internal-network topology is an
implementation of the already-open proxy and sandbox design: the non-root workload has no direct
egress, while the separately constrained proxy owns allowlisting and observation. Any need to alter
the Evidence schema, Observer contract, verdict function, or P-009 boundary stops for a proposal.

## Working method

Preserve unrelated work. Implement the smallest complete capability behind the existing injected
pack contracts. Prefer immutable value objects, deterministic serialization, narrow subprocess
boundaries, fake runtime tests for error surfaces, and a small number of real rootless Podman
integration tests for claims mocks cannot establish. Do not weaken a check to accommodate the host.

## Stop and escalate conditions

Stop and report the evidence if the measured rootless Podman engine cannot execute a non-root
isolated test or the mandatory rlimits cannot be proved; a required image cannot be acquired without credentials or
material cost; the workload cannot be prevented from bypassing an allowlist; the disposable CA or
proxy private key cannot be confined to the validated per-run boundary; a frozen schema/interface must change;
sentinel independence cannot be enforced; a target test requires production credentials; or a
prospect repository would need modification.

## Open questions

None. The frozen architecture chooses the two capture products, injected pack boundaries, shared
event contract, proxy-default policy, sandbox model, telemetry behavior, and promotion location.
Implementation choices remain open inside those hard edges. P-009 is a pending owner decision but
does not block Phase 4 because AI triage remains operationally disconnected.
