# Deploying Counterpart

The base topology is a private, single-host service: PostgreSQL 17, a nonroot API,
separate nonroot workers, and a durable shared media volume. It does not publish a
database port, start a model, enable provider calls, or expose the application on
the public network. The API binds host loopback. The optional TLS overlay is an
explicit deployment action, not part of starting the local stack.

## Evidence and limits

On 2026-09-26, the S3 adapter was exercised against an actual localhost Moto HTTP
server, including concurrent writes and response loss. Base and TLS Compose files
were parsed with official Docker Compose v5.5.1 and inspected for private port
binding and required operator authentication. Image tags and multiarchitecture
manifest digests were checked against Docker Hub.

The actual locked image was subsequently built and exercised in a **new isolated
Colima Linux/arm64 VM**, with 2 CPUs, 2 GiB RAM, an 8 GiB data disk and 6 GiB root
disk. The stopped `dbprobe` profile and native app on port 8080 were left alone.
Host mounts, SSH-agent forwarding and automatic context activation were disabled.
The private test stack used port 18080, generated disposable credentials, and no
provider keys; inference and external export were disabled.

Executed container checks: PostgreSQL/API/worker initialization and readiness,
operator login and anonymous denial, collector invitation, complete fixture pair,
worker SIGKILL with baseline preservation, work queued while the worker was
stopped and completed after restart, idempotent publication, explicit review,
immutable release, original-byte hash readback, and a complete service restart
preserving the session, review, release and media. API UID was 10001. Actual
Chromium at 390px opened the compiled frontend, required login, rendered both
saved images and downloaded the 71,123-byte dataset ZIP without page errors or
horizontal overflow. Script/style MIME types and absence of the operator token
from browser storage were verified. These are fixture/runtime checks, not model
accuracy or human camera evidence.

The dedicated containers, volumes, VM and temporary secret/tool files were removed
after the check. [Machine-readable evidence](../research/deployment_probe.json)
records the exact image and source revision. The HTTP acceptance script is
[deployment_probe.py](../scripts/deployment_probe.py); compiled browser checks are
in [browser_probe.mjs](../deploy/browser_probe.mjs).

**Still not executed:** public TLS certificate issuance, a public deployment or
hosted S3-provider compatibility. No cloud storage was provisioned and no model
calls or charges were incurred during the deployment gate.

## Private container setup

Requires Docker Engine/Desktop with Compose and enough resources for ordinary web
services; there is no local GPU, inference runtime, or model download. The pinned
image includes Python 3.13.15 and the frontend built with Node 24.21.0. Python and
npm dependencies resolve from the committed lockfiles; runtime contains neither
Node nor development Python packages.

From the repository root:

```sh
python3 deploy/prepare.py
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" config --quiet
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" up --build -d
```

`prepare.py` generates random database/operator secrets in owner-readable files
outside the repository. It never overwrites an existing setup or prints secrets.
The startup initializer runs before the API and worker; PostgreSQL has a readiness
probe, and API health exercises its database path. A running worker container is
only process liveness: check the operator queue and complete a replay capture to
prove work consumption. The shared media directory is initialized for UID/GID
10001; external bind mounts need matching permissions.

Open `http://127.0.0.1:8080`. Read the operator token privately from
`~/.config/counterpart/deploy/runtime.env` to sign in. Do not paste configuration
into logs, issue reports, or `docker compose config` output; use `config --quiet`.
The loopback-only stack explicitly enables `operator_local_http`; the exception
requires loopback origins and restricted hostnames. The TLS overlay disables it.
Operator access and collector invitations are distinct from each session's
capability. Session links are secrets and must not be posted publicly.

The generated config disables live inference and external export. To enable a
bounded Roboflow deployment, edit the private `runtime.env`, set
`COUNTERPART_LIVE_ENABLED=true`, and add `ROBOFLOW_API_KEY` and
`ROBOFLOW_WORKSPACE`. Verify the account's current free-credit/charge controls
before doing so. `COUNTERPART_INFERENCE_LIMIT` is the lifetime durable image-call
allowance in this database, not a monthly allowance. Keep its budget reservations
when restoring or migrating. Retried/ambiguous calls remain counted. Do not enable
Roboflow export to a public project for personal camera captures.

## Phone capture and TLS

A phone visiting an ordinary LAN HTTP address does not get the browser's
localhost camera exception. Serve HTTPS with a certificate the phone trusts.
The Caddy overlay uses automatic HTTPS for a real hostname; its HTTP listener is
for certificate challenges and redirect only. Before opening a firewall, finish
the private replay gate, verify operator login/invite admission, choose an allowed
origin, and keep provider limits configured. No public deployment has been
performed by the build agent.

Set `COUNTERPART_DOMAIN` in the private `compose.env`, create the domain's DNS
record for the chosen host, and allow ports 80/443 only after choosing to expose it:

```sh
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" \
  -f compose.yaml -f deploy/compose.tls.yaml config --quiet
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" \
  -f compose.yaml -f deploy/compose.tls.yaml up --build -d
```

The overlay enforces secure operator cookies, the HTTPS origin and a hostname
allowlist. Caddy forwards to the private API and persists certificate state. It
does not enable access logging, which could expose capture URLs. With Cloudflare,
use Full (strict) TLS and a reachable origin; DNS alone does not host this backend.
Cloudflare Pages alone cannot run the PostgreSQL/worker portion. Local loopback
remains bound for debugging, but secure cookies require using the HTTPS origin
for operator operations once the overlay is active.

## S3-compatible durable media

For workers on multiple machines, use S3 storage or an actually shared persistent
filesystem. A machine-local directory with the same pathname is not shared data.
The database remains the only authority for which complete pairs exist. The
browser never receives storage credentials or a presigned bucket URL: authorized
API reads return verified media.

Create a **private** bucket separately, enable suitable encryption/versioning and
backups, and give the runtime principal only object read and conditional write
access to the configured prefix. `deploy/s3-policy.example.json` is an AWS IAM
example; replace the bucket and adapt for provider-specific policy support.
There is no bucket-creation API, public ACL, or remote URL intake in
`S3MediaStore`. Listing/deletion methods are for explicit maintenance under the
exclusive database reference lock, using a separate identity with scoped
`s3:ListBucket` and `s3:DeleteObject` permissions. Runtime credentials use boto3's standard workload identity /
environment chain. Prefer workload identity; if using credentials, place them
only in private runtime configuration:

```dotenv
COUNTERPART_STORAGE_BACKEND=s3
COUNTERPART_S3_BUCKET=your-private-bucket
COUNTERPART_S3_PREFIX=counterpart/media
COUNTERPART_S3_REGION=us-east-1
# Optional operator-selected S3-compatible origin, HTTPS only:
# COUNTERPART_S3_ENDPOINT_URL=https://your-storage-endpoint.example
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
```

Every image is validated before upload and addressed by its original-byte
SHA-256, under `prefix/first-two-hex-digits/full-digest`. Uploads use
`If-None-Match: *` and a SHA-256 transport checksum. A 412 reads and verifies the
already-stored bytes; 409 is retried a bounded number of times. A lost response
cannot silently overwrite the object on retry. Unconditional writes are never a
fallback for an incompatible provider. All reads bound the body size and verify
the digest; ETag and object metadata alone are not trusted as integrity proof.

S3 compatibility varies. Before using a provider, run real conditional-write,
concurrent-replay, response-loss and readback tests against a dedicated bucket
within an explicit budget. Moto tests do not prove that a particular hosted
provider implements those semantics. An administrator with wider credentials can
still overwrite/delete data; application immutability is not WORM compliance.
Do not apply blanket bucket expiration rules: accepted pairs retain asset
references. Retention must reconcile references first and use a separate
maintenance identity. Versioned-bucket deletion creates a delete marker; old
versions and their storage charges remain. This is not a historical-byte purge.

Changing storage backend does not migrate historical assets. Pause API/workers,
copy and verify all referenced blobs without changing digests, preserve the
database and allowance ledger, then switch both API and every worker together.
Test old-pair downloads before admitting new collection.

## Operation, backup, restore and rollout

Upload and ZIP spools use a dedicated disk-backed `scratch` volume through
`TMPDIR=/data/scratch`; they do not consume the container's small `/tmp` RAM mount.
Temporary files close after requests and process exits. Provision disk for concurrent
uploads/downloads and monitor available space. The default maximum archive is512MiB;
release smaller groups of complete pairs above that limit. Scratch is disposable and
must not be treated as original evidence or included in evidence backups.

Keep API and worker from the same image revision. Scale workers with
`--scale worker=2`; transaction/lease admission still bounds concurrent committed
verification. Adding workers does not increase a provider allowance. Monitor
pending age, expired leases, recovery failures, budget rejections and export
reconciliation; container restarts alone are not proof of useful work. The
operator runtime endpoint reports database-clock worker heartbeats (5-second
refresh, stale after 15 seconds); this is liveness, not provider readiness.

The typed backup/restore procedure and its executed local PostgreSQL restoration
drill are in [OPERATIONS.md](OPERATIONS.md). The application image intentionally
does not bundle the PostgreSQL administration client. Run the typed backup helper
from an administrative environment with compatible `pg_dump`, or use the
container-native procedure below, which runs `pg_dump` from the PostgreSQL image.
The scratch volume supports bounded archive spooling without assuming that a
512 MiB archive fits a small memory-backed `/tmp`.

For a consistent filesystem backup, stop API and workers, dump PostgreSQL and
copy the media volume while capture/retention writes are stopped. For example,
with `umask 077` and an existing private backup directory:

```sh
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" stop api worker
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" exec -T postgres \
  pg_dump -U counterpart -d counterpart -Fc > /private/backup/counterpart.dump
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" \
  cp api:/data/media /private/backup/media
docker compose --env-file "$HOME/.config/counterpart/deploy/compose.env" start api worker
```

Use a real private path; the paths above are placeholders. Check every command's
exit code and restart services even if a backup fails. Protect copies as camera
data. For S3, retain the bucket or a verified independent copy plus the exact
storage config; a database dump alone does not contain the media.

Restore into an **isolated empty database and empty storage target**, never the
running service. Use `pg_restore --exit-on-error --no-owner`, restore media with
the correct ownership, then start one worker and verify old pair downloads,
SHA-256 readback, preserved budget reservations, invitation/session access, and a
new replay capture. Only switch traffic after that rehearsal passes. The local
PostgreSQL test has restored a complete reviewed pair, release, capability and
nonzero reservation ledger using real `pg_dump`/`pg_restore` in isolated schemas.
The container gate verified full service restart and named-volume persistence;
it did not restore a container stack from an off-host backup or reconcile live
provider-side effects.

Before an update: back up, record the image revision, stop admission during schema
changes, run initializer, then roll API and workers together. An application image
rollback does not automatically roll back schema/data changes; use the tested
restore procedure if the old image is incompatible. Never use `down --volumes`
as a routine restart: it destroys evidence and the spending ledger.

## Reused implementations and sources

- [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
  and [PutObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html):
  native conditional creation and conflicts, rather than a racy HEAD-then-PUT lock.
- [AWS policy enforcement](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html):
  optional infrastructure enforcement of the same write precondition.
- [Boto3](https://github.com/boto/boto3), Apache-2.0, pinned 1.43.103; standard
  SigV4 transport, credentials and bounded retry implementation.
- [Moto server mode](https://docs.getmoto.org/en/latest/docs/server_mode.html),
  Apache-2.0, pinned 5.2.3; local HTTP contract testing, not a production store.
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) and
  [reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy):
  certificate management and HTTPS termination, rather than custom TLS code.
- Official [Python](https://hub.docker.com/_/python),
  [Node](https://hub.docker.com/_/node), [Postgres](https://hub.docker.com/_/postgres)
  and [Caddy](https://hub.docker.com/_/caddy) image manifests, exact digests pinned.

Targeted Exa search was also used to find/verify the AWS primary documentation.
No third-party application source was copied; package/source licenses remain with
their respective projects.

## Executed HTTPS and expanded-runtime rehearsal

The expanded build passed an additional actual Linux/arm64 container gate on
2026-09-27 UTC. Its source fingerprint, image identity, checks and cleanup receipt
are in [gap_deployment_probe.json](../research/gap_deployment_probe.json). The
[earlier successful rehearsal](../research/gap_deployment_probe.initial.json)
remains recorded separately, before the saved-comparison explanation was improved.
These are runtime and deterministic-fixture results, not model-accuracy results.

The new `counterpart-gap-test` Colima profile was capped at two CPUs, 2 GiB RAM,
an 8 GiB data disk and 6 GiB root disk. It used no host mounts, SSH forwarding,
user VM template, Kubernetes or Docker context activation. The existing stopped
`dbprobe` profile and native application were preserved. The frozen image was
built from lockfiles, and PostgreSQL, API, worker and the actual Caddy reverse
proxy ran together. All nine added grouping, evidence and delivery tables were
present in the initialized database.

The private test proxy exposed only loopback port 18443. Python explicitly
validated the generated certificate and `counterpart.test` server name while
connecting to loopback. Chromium resolved that reserved test hostname to loopback
and trusted only the ephemeral certificate's public-key fingerprint in that
browser process. General certificate-error suppression was disabled and the host
OS trust store was unchanged. The disposable Caddy container's internal trust
state stayed inside the removed VM; it was not a host trust installation.

Executed HTTPS checks included anonymous operator refusal, secure HttpOnly
SameSite=Strict operator cookies, rejected foreign-origin login, security headers,
collector admission and capability separation, collection/review/release, worker
SIGKILL, durable queued work, complete service restart, original image hashes and
immutable release readback. The runtime imported OpenCV 4.13.0 and NumPy 2.5.3,
executed the version-2 visual checks on saved fixture evidence, and confirmed
single-thread OpenCV configuration. The fixture baseline's registration became
uncertain under version 2. Its exact decisions remained unchanged; the appended
comparison explicitly reported that the counterpart check was blocked by the
uncertain revised reference. The revision survived service restart without
rewriting the capture or previously released dataset.

Actual Chromium at 390 × 844 opened the compiled application over the non-localhost
HTTPS origin, downloaded the 71,919-byte fixture release, inspected the real saved
comparison history and expanded the revised baseline checks. It displayed the
uncertainty explanation without page errors or horizontal overflow. Native
`getUserMedia` returned a synthetic 640 × 480 video track under the page's camera
permission policy. This establishes HTTPS/browser media plumbing; it does not
exercise a physical camera or validate detector quality. Live inference and
external export were disabled, provider credentials were absent, and the
inference dispatch ledger stayed empty.

The dedicated containers, volumes, VM, certificate keys and temporary tools were
removed. The Docker context and pre-existing profiles were unchanged, and the
native application still returned HTTP 200 on port 8080.

Reproduction is deliberately scoped to Apple Silicon with Colima, Docker and the
repository's development dependencies/browser already installed. Use a fresh
private directory outside the checkout, a stable source tree, and no existing
`counterpart-gap-test` profile. The existing native application on port 8080 is
checked separately during cleanup. The harness downloads SHA-verified official
Compose and Buildx tools into its private directory and never prints credentials:

```sh
uv run python deploy/gap_probe.py prepare --directory /tmp/counterpart-https-rehearsal
uv run python deploy/gap_probe.py start --directory /tmp/counterpart-https-rehearsal
uv run python deploy/gap_probe.py build --directory /tmp/counterpart-https-rehearsal
uv run python deploy/gap_probe.py check --directory /tmp/counterpart-https-rehearsal
uv run python deploy/gap_probe.py cleanup --directory /tmp/counterpart-https-rehearsal
```

Run cleanup even if the check fails; it targets only the profile bearing that
run's ownership marker. Its guard regressions cover conflicting profiles,
unowned directories, missing teardown components and cleanup despite a separate
native-health failure. Public certificate issuance, public hosting and hosted
S3-provider compatibility remain distinct external checks.
