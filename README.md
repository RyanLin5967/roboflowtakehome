# Counterpart

Collect two real scenes. Change one specified relationship. Keep the evidence together.

Counterpart guides a collector through making controlled visual pairs with an ordinary camera, uses Roboflow to check the objects and named controls, and asks for a specific repair while the scene is still available. Its infrastructure preserves completed work across interruptions, rejects stale results, bounds preview work and publishes complete, reproducible records.

The first recipes are **Switch sides** (bottle left/right of a stationary cup) and **Reverse the order** (same start and end arrangement, opposite order entering a target area). The temporal label concerns observed arrival intervals, not unseen exact movement onset.

## Run locally

Requirements: Python 3.12, `uv`, Node 22+ with npm, PostgreSQL 17+. No local model, CUDA, PyTorch or training is required.

```sh
createdb counterpart_dev
createdb counterpart_test
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run counterpart init
```

Run these in two terminals:

```sh
uv run counterpart serve
```

```sh
uv run counterpart worker
```

Open **http://127.0.0.1:8080**. Deterministic replay exercises the real API, database, worker, verification and publication path without model spending. It is visibly marked as an illustration and its manifest identifies the fixture provider.

For live camera collection, set `COUNTERPART_LIVE_ENABLED=true` in both processes and provide a server-only `ROBOFLOW_API_KEY`. The preexisting private credential file is supported; never put secrets in this repository. The default allowance is 128 image attempts total, reserved durably in PostgreSQL before dispatch. Recreating the database resets that local counter; provider billing controls still matter. A private runtime config at `~/.config/counterpart/runtime.json` can hold nonsecret settings. Temporal clips reserve their entire frame allowance up front. Every retry gets another reservation; ambiguous calls remain counted.

Live inference uses Roboflow's hosted RF-DETR Small (`coco/39`). A camera needs localhost or HTTPS; plain HTTP on a phone's LAN address is not a secure context. A remote API/worker deployment must share persistent database/media storage. This checkout defaults to localhost and is not a hardened public multi-tenant service.

## What is built

- Browser capture with recoverable upload identity, explicit replay/live modes and targeted correction.
- Transactional capture state, immutable media hashes, idempotency and stale-result fencing.
- Separate policies for disposable previews and durable sampled temporal evidence.
- Fenced leased workers and a durable model-call allowance.
- Versioned deterministic verification with uncertainty and named controls.
- Atomic local pair publication, complete ZIP/JSON evidence, VQA JSONL and session-level split grouping.
- An optional Roboflow export adapter/state machine for an existing configured project. Disabled by default. External export requires separate real-provider validation; a local downloaded pair does not imply remote export succeeded.

No method here proves all physical properties stayed fixed or that every model prediction is correct. Collection thresholds require validation on actual camera scenes. See the execution record for what has actually been tested.

## Verify

```sh
COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest -q
uv run ruff check src tests
npm --prefix web run build
```

The tests use temporary schemas in the dedicated test database. They do not train or run a model. Browser tests and provider smoke are reported separately.

## Design and provenance

- [Architecture](docs/plans/ARCHITECTURE.md) and [protocol](docs/plans/PROTOCOL.md)
- [Validation plan](docs/plans/VALIDATION.md) and [independent review](docs/plans/REVIEW.md)
- [Infrastructure reuse](research/infra_reuse.md), [Roboflow API/source research](research/cv_reuse.md), [capture reuse](research/capture_reuse.md)
- [Execution/restart record](EXECUTION_STATUS.md), [verification record](docs/VALIDATION_REPORT.md), [presentation](docs/DEMO.md)

Counterpart reuses established database/lease/outbox techniques, browser camera APIs and Roboflow's detector. Controlled-pair datasets and guided collection have substantial prior art; the product contribution here is their concrete integration into recoverable collection with repairable evidence. It does not claim invention of those methods or exactly-once remote execution.
