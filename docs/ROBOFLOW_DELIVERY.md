# Roboflow annotation review and dataset delivery

Counterpart now carries an approved local release through Roboflow annotation review and a hosted dataset-version intent. Returned annotations are separate, immutable evidence. Neither a corrected box nor a later reviewer decision rewrites an original detector observation, machine refusal, human adjudication or published local release.

## Operator workflow

1. Create and approve a Counterpart release. Its source images, pair membership and partitions are frozen.
2. Configure a **dedicated private Roboflow project for this release** (`COUNTERPART_EXPORT_PROJECT`, export enabled, credentials outside the repository). The existing export action uploads original images and detector boxes as predictions only: `prediction=true`, `addToDataset=false`. A human-adjudicated pair does not make its uncertain detector boxes ground truth.
3. Correct or accept boxes in Roboflow Annotate and add the reviewed images to that Roboflow dataset. The provider review UI is external. Counterpart does not infer that an annotation was human-reviewed merely because it exists.
4. In the release's Roboflow panel, pull an annotation snapshot. Counterpart reads the actual annotation from Roboflow's image-details API, not the search endpoint's annotation-count summary. It binds each source hash, provider image ID, dimensions, split, returned raw annotation and normalized box set to the immutable local release. New pulls append snapshots. Repeating an identical request key returns the original snapshot, even if annotations have since changed.
5. Inspect the returned annotations and approve the snapshot explicitly in Counterpart with a reason. This approval is distinct from the earlier pair review and from Roboflow's internal workflow status.
6. Create a delivery intent. Generation requires a separately configured included-credit allowance and current evidence of provider billing controls. It is disabled by default. Before generating, the project must contain **exactly** the selected images, every image must be in its dataset, and identity, partitions and annotations must still match the approved snapshot.
7. Advance the delivery. One durable generation reservation is committed before the one external generation request. No augmentation or preprocessing is requested, so relation labels, partitions and box geometry are not intentionally transformed. A successful response records the provider version but does **not** mark contents verified.
8. Refresh hosted status until that version has finished generating with the expected image and partition counts. Download its original-size COCO export through Roboflow, and import the `train`, `valid`, and `test` `_annotations.coco.json` documents in the Counterpart panel. Counterpart validates exact source membership, partitions, dimensions and corrected boxes. Extra images, missing images, changed labels or boxes, duplicate identities, malformed geometry and unsupported segmentation are refused.

A project shared with unrelated images intentionally fails the generation gate. Do not delete unrelated data to satisfy it. Use a dedicated private destination and re-export the selected release there through an intentional configuration change. Previously frozen delivery records retain their original destination; changing the configured project does not redirect them.

The configured destination is single-workspace infrastructure, not an arbitrary URL or browser-controlled project selector. Camera export remains off on the current public-only workspace. Local release downloads and live Roboflow perception do not require private dataset export entitlement.

## What is actually verified

`generated` means that Roboflow acknowledged a version number. `verified` means a ready hosted version was observed and the operator-imported COCO annotation documents exactly matched the approved local source selection and corrected boxes. The artifact receipt explicitly says `provider_authenticated_artifact=false` and `remote_pixel_identity_verified=false`: importing JSON cannot prove its download origin or remote image pixel identity. Original pixels remain independently hash-verified in the local Counterpart archive. This distinction is visible in the product; the feature does not claim cryptographic provider attestation.

Provider metadata is not a transactional snapshot. Two inventory reads detect observed membership changes during annotation collection, and generation rereads annotations after approval. Roboflow does not offer an atomic inventory-lock-and-generate operation through this adapter. Final COCO membership verification catches observed generation contamination, but another project administrator can still race external changes. Restrict writers on a dedicated release project.

Raw annotation snapshots preserve the image API's pixel-center boxes. Canonical normalized boxes use pixel `[left, top, width, height]`. Unsupported polygons, masks, keypoints or unknown geometry fail closed rather than being silently discarded. The current feature is for the project's object-detection data, not every Roboflow annotation type.

## Failure and recovery semantics

- `planned`: no generation reservation or external generation write yet. Configuration and inventory can be repaired safely before advancing.
- `issuing`: the durable uncertainty boundary was committed. A worker/API crash here must never cause a blind second generation request.
- `uncertain`: the provider response was lost or ambiguous. The allowance remains spent locally; refresh performs reads only.
- `failed`: a definite provider refusal. Its reserved attempt remains counted conservatively. A new approved snapshot can create another intent only within the remaining server allowance.
- `generated`: acknowledged version; ready-state and artifact verification still required.
- `verified`: immutable explicit artifact verification receipt recorded.

Newly observed provider versions are only reconciliation candidates; they are not automatically adopted because another operator could have generated them. An operator can reconcile an uncertain/issuing intent by importing a matching COCO export for a newly observed, ready candidate. The receipt records that the original write acknowledgement was **not** recovered. Existing versions from before the generation attempt cannot be claimed through this path. No recovery path resets or refunds an ambiguous reservation.

Concurrent advances serialize the reservation under PostgreSQL locks. Separate intents share a global persisted attempt allowance. Lost responses, duplicate requests, changed annotations, public destinations and unrelated images have dedicated tests. The original export outbox still reconciles uncertain image uploads without repeating them. An ambiguous prediction-write outcome is not automatically treated as the intended prediction; its separate export receipt remains conservative.

## Configuration and limits

`COUNTERPART_ROBOFLOW_GENERATION_LIMIT` defaults to `0` and is bounded to `0..100`. `COUNTERPART_ROBOFLOW_GENERATION_BUDGET_EVIDENCE` is a short nonsecret operator reference to current included-credit availability and no-cash billing controls, never an API key. It is hashed in reservation records. Raising an application attempt limit does not configure or guarantee a provider spending cap. Restored database allowances require provider-usage reconciliation before writes resume.

Each delivery is limited to 1,000 source images. Provider reads and JSON bodies are bounded. Snapshot and COCO metadata are limited to two MiB. API credentials are server-side; returned provider URLs and HTTP error bodies are excluded from snapshots and logs. All routes require operator authentication.

Generation consumes credits. The production allowance remains zero. After the user explicitly reaffirmed available Roboflow credits, a separate one-attempt generated-fixture probe rechecked `/usage/plan` (`is_pro=false`, `is_enterprise=false`, `is_trial=false`, `over_quota=false`) and the official free-plan exhaustion behavior. One no-augmentation, one-image version was reserved durably before dispatch. Its estimated generation use is 0.00005 credits at the published 20,000-images-per-credit rate; this is an estimate, not a retrieved billing receipt. No training or Modal job was launched. The private-project entitlement recheck still returned 422, so this probe used only the explicit public generated-card project and did not weaken production privacy checks.

## Provider contract and executed evidence

The implementation follows the [official Roboflow Python project's pinned source](https://github.com/roboflow/roboflow-python/blob/c39f99e6645b9039188f16665a5e17b2d83655d1/roboflow/core/project.py): project metadata via GET, paginated image search via POST, image details via GET, and `generate_version` via POST with preprocessing/augmentation settings. The [official version implementation](https://github.com/roboflow/roboflow-python/blob/c39f99e6645b9039188f16665a5e17b2d83655d1/roboflow/core/version.py) distinguishes generating versions from ready exports. No invented provider annotation-review or transactional-generation API is used.

Roboflow's [published credit explanation](https://blog.roboflow.com/roboflow-faqs/) identifies dataset generation as a credit-consuming action. Its [pricing page](https://roboflow.com/pricing) describes the public plan and private-data entitlements. These pages do not establish this account's current remaining credit balance.

A bounded real hosted probe uploaded one explicitly generated 800×480 card to the existing public integration-test project, wrote one synthetic fixture annotation, and read back the exact expected box and test split. This is transport/coordinate evidence, not human capture or model-accuracy evidence. An earlier annotation attempt on the pre-existing generated card had an ambiguous outcome; it was not repeated and readback remained empty. Sanitized evidence is [roboflow_annotation_probe.json](../research/roboflow_annotation_probe.json). No camera image was made public.

Real PostgreSQL tests cover immutable corrected snapshots, concurrent retries, reviewer separation, generation allowance races, crash uncertainty, exact inventory, artifact verification and v2 human-adjudicated export. Mocked transport tests verify HTTP methods, paths and payloads; these are separate from the actual generated-card hosted probe.

## Actual hosted version probe — 2026-09-27 UTC

[roboflow_version_probe.json](../research/roboflow_version_probe.json) records one reserved generation attempt, the complete frozen two-card inventory (only one annotated card was in the provider dataset), observed ready version 1, and a genuine 10,122-byte COCO archive downloaded from the provider-returned official signed URL. The signed URL was never persisted. Exact dataset membership, the test partition, 800×480 dimensions and the corrected rectangle match the returned COCO documents, preserved in [roboflow_version_probe_coco.json](../research/roboflow_version_probe_coco.json). Raw generated-image ZIP bytes are kept only in ignored local artifacts.

The generation acknowledgement did not satisfy the adapter's integer-only version contract, so the write remained ambiguous. It was **never repeated**. Read-only reconciliation observed the new candidate version and downloaded its official artifact; the report preserves `generation_receipt_confirmed=false` rather than inventing a recovered write receipt. The exact unsupported acknowledgement value was not retained, so its precise type is not asserted. Compatibility now accepts bounded numeric-string versions, as the official SDK does. Actual metadata also omitted empty partition keys (`{test: 1}`); the adapter now normalizes absent train/valid counts to zero. Both forms have regression tests, including the actual returned COCO metadata.

This is genuine hosted generation/export and coordinate-contract evidence on generated fixtures. It does not establish human capture accuracy or private production end-to-end delivery. The one-attempt ledger lives outside the repository at `~/.config/counterpart/provider-probes/hosted-version-20260927.json` with its sanitized copy in the report. Re-running `uv run python scripts/roboflow_delivery_probe.py` resumes reads and artifact verification; it cannot send a second generation request while that ledger exists. Do not remove the ledger to bypass its allowance.
