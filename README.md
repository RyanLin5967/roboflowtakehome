# Counterpart

**Collect the difference while the scene is still there.**

Counterpart guides people to capture two related images or clips, checks the requested change with Roboflow, and releases reviewed pairs with their original evidence. If a collector changes the wrong thing, it preserves the first observation and explains what to repair.

An operator defines a collection → a collector joins a scoped invitation → Roboflow detects the objects → a versioned recipe checks named controls → a human reviews the pair → an immutable dataset release carries the images, observations, decisions and labels together.

Two recipes are implemented:

- **Switch sides:** move one object from left to right of a stationary reference.
- **Reverse arrival order:** reach the same final arrangement in opposite orders. The label describes observed arrival at a target band, not an unseen exact movement onset.

Live recipes support distinct roles drawn from bottle, cup, book, cell phone and mouse, plus optional protected objects. Version 2 checks image registration and appearance alongside geometry and timing, with separate passed, failed and unknown conditions. These checks cannot prove physical identity or that every property stayed constant.

## Run

Python 3.12–3.13, `uv`, Node 22+, and PostgreSQL 17+. No local model, PyTorch, GPU or training is required.

```sh
createdb counterpart_dev
createdb counterpart_test
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run counterpart init
```

In separate terminals:

```sh
uv run counterpart serve
```

```sh
uv run counterpart worker
```

Open **http://127.0.0.1:8080** for capture, or **http://127.0.0.1:8080/#/workspace** to create a collection. Local operator access bootstraps only in explicitly local mode. Labeled replay follows the real application/database/worker path without calling a model; its exports remain identified as fixtures.

For real collection, provide a server-only `ROBOFLOW_API_KEY` and enable `COUNTERPART_LIVE_ENABLED=true` for API and worker. Credentials may live in the existing private credential file; keep them outside this repository. Hosted `coco/39` performs perception. Every actual image dispatch reserves a durable allowance before its request; retries never erase uncertain cost. Successful frame observations survive process crashes and are reused only for identical bytes and detector configuration. The portable default allowance is 128 image attempts; changing or restoring the database is **not** a way to reset provider spending.

A phone camera requires HTTPS. The [deployment guide](docs/DEPLOYMENT.md) includes authenticated API/worker/Postgres containers, shared filesystem or S3 media, and Caddy TLS. An isolated local HTTPS deployment passed certificate verification, secure cookies, the compiled mobile app, camera plumbing, v2 native visual checks, worker recovery and restart persistence. Public hosting and public certificate issuance have not been executed.

## The complete data path

- Frozen collection recipes and detector configuration; incompatible workers refuse continuation before spending credits. Expiring, revocable invitations enforce quotas and can share a scene group across collections.
- Durable collector library, original saved evidence, targeted repairs, scene rechecks after reconnect, and retry of saved frames after provider failure.
- Per-frame inference checkpoints, shared dispatch limits, fair scheduling, cost reservations, progress and worker liveness.
- Append-only re-verification of saved observations. Uncertain attempts can receive explicit human adjudication without rewriting machine refusals; known failed controls remain blocked. A reviewed uncertain first image can authorize further capture, but the resulting pair still requires adjudication and separate release review.
- Explicit dataset releases with reproducible membership hashes, original media, VQA JSONL, frozen decisions and shared scene-group splits. Related sessions stay together, and identical assets cannot cross release partitions.
- Optional Roboflow dataset export, explicitly requested for approved live pairs and bound to a server-verified private destination. Each new upload rechecks privacy. Receipts and bounded reconciliation remain visible; ambiguous annotation writes are never blindly repeated.
- Returned Roboflow annotations can be inspected and approved before a separately budgeted hosted dataset version. Version generation, provider readiness and independently checked COCO contents have distinct receipts; an uncertain write is reconciled without another generation request.
- Immutable filesystem/S3 storage, authenticated operator boundaries, streamed/spooled bounded downloads, safe orphan cleanup, and tested backup/restoration.

A portable ZIP works without the service or database. [Dataset format and consumer](docs/DATASET_FORMAT.md) describe validation and reading. Downloads default to a 512 MiB limit; release smaller groups of complete pairs for larger collections. The manifest remains available independently.

## Verify

```sh
COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest -q
uv run ruff check src tests scripts
npm --prefix web run build
npm --prefix web test
```

With API and worker running:

```sh
node scripts/browser_smoke.mjs
node scripts/browser_product_smoke.mjs
node scripts/gap_browser.mjs
uv run python scripts/inspect_release.py artifacts/gap-browser/dataset.zip --allow-replay
uv run python scripts/product_fault_probe.py
```

Tests use temporary PostgreSQL schemas and a local S3 protocol emulator. They do not run local models. The fault probe uses real child-process termination and explicitly synthetic provider observations. Actual hosted inference and physical collector evaluation are reported separately in the [verification record](docs/VALIDATION_REPORT.md); no general CV accuracy or collection-efficiency claim follows from passing software tests.

## Design and evidence

- [Complete-product contract](docs/plans/FULL_PRODUCT.md), [architecture](docs/plans/ARCHITECTURE.md), [protocol](docs/plans/PROTOCOL.md)
- [Inference runtime](docs/plans/INFERENCE_RUNTIME.md), [recipes](docs/plans/RECIPES.md), [operations](docs/OPERATIONS.md)
- [Frozen execution](docs/plans/EXECUTION_CONTRACT.md), [visual verification](docs/plans/VISUAL_VERIFICATION.md), [evidence revisions and adjudication](docs/EVIDENCE_REVISIONS.md)
- [Roboflow annotation and version delivery](docs/ROBOFLOW_DELIVERY.md), [feedback comparison](docs/FEEDBACK_BENCHMARK.md), [original-scope audit](docs/ORIGINAL_SCOPE_AUDIT.md)
- [Modal GPU diagnostic](docs/MODAL_VALIDATION.md), [historical migrations and restore](docs/GAP_MIGRATION_EVIDENCE.md), [executed validation](docs/VALIDATION_REPORT.md)
- [Physical validation protocol](docs/plans/PHYSICAL_VALIDATION.md), [independent review](docs/plans/COMPLETENESS_REVIEW.md)
- [Infrastructure reuse](research/infra_reuse.md), [Roboflow source research](research/cv_reuse.md), [capture reuse](research/capture_reuse.md)
- [Execution record](EXECUTION_STATUS.md), [build loop](BUILD_LOOP.json), [presentation](docs/DEMO.md)

Counterpart combines established collection methods, database leases/outboxes, browser camera APIs and Roboflow perception. Its contribution is the integrated collection-and-repair workflow and auditable data path. It does not claim to invent paired datasets or guarantee exactly-once external computation.
