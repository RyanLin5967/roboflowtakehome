# Portable Counterpart datasets

A dataset release is a ZIP with `manifest.json`, `examples.jsonl`, and original images at `assets/<sha256>`. Both members of a pair stay together. Temporal examples contain the original sampled frames in timestamp order, not an invented video or guessed event time. Assets are deduplicated by exact bytes.

A pair has schema `counterpart.pair.v1` when its labels come from accepted machine evidence, or `counterpart.pair.v2` when an explicit human adjudication resolves eligible perceptual uncertainty. The pair schema version is separate from the recipe/verifier version: a machine-accepted pair can use the newer visual checks and still be a `counterpart.pair.v1`.

A reviewed collection release has schema `counterpart.dataset.v1`. It contains the selected immutable pair manifests plus the exact approved review version for each pair. Human adjudication, ordinary pair approval and release creation are separate decisions. A newly adjudicated pair starts with ordinary review pending; it is not automatically ready for release. Later reviews and verifier comparisons do not rewrite an earlier release.

## Use the data immediately

Validate a downloaded release without a database, network request, or model:

```sh
uv run python scripts/inspect_release.py /path/to/release.zip
# An illustrative replay download requires explicit opt-in:
uv run python scripts/inspect_release.py /path/to/replay.zip --allow-replay
```

The reader validates the complete archive before yielding any examples. Nothing is extracted to the filesystem. Use Counterpart's locked Python environment: Pillow verifies image encoding, and recipes with visual clauses use the bounded classical image checks over original pixels. Validation runs no trained model and makes no provider calls.

```python
from counterpart.consumer import PortableRelease

with PortableRelease("release.zip") as dataset:
    print(dataset.summary)
    for example in dataset.iter_examples(split="train"):
        # Preserve pair_id and split_group in the downstream data loader.
        images = [dataset.read_media(frame["path"]) for frame in example["media"]]
        question = example["question"]
        answer = example["answer"]
        timestamps_ms = [frame["timestamp_ms"] for frame in example["media"]]
        # Hand these original bytes and labels to your existing data pipeline.
```

`iter_examples` yields detached row copies. `read_media` reads only validated archive members and checks the hash again, so changing the archive after initial validation cannot silently alter returned evidence. A supplied binary stream remains owned by its caller. A path opened by the reader is closed when the context exits.

## Rows and provenance

Every `counterpart.vqa.v1` row identifies its capture, pair, member (`baseline` or `counterpart`), split group, partition, question, observed answer, verifier version, evidence source, and ordered media list. Media entries carry original byte SHA-256, dimensions, and timestamp in milliseconds. The frame's full byte count, MIME type, detector identity, and recorded observations are in the pair manifest.

- Machine-derived spatial answers come from the recorded left/right relation. They are not inferred solely from what the collector was asked to do.
- Machine-derived temporal answers name the first object whose observed arrival interval establishes entry into the target band. The question preserves the frozen 62%-of-image-width band definition. Human-derived rows use the adjudicator's explicitly recorded answer under the same frozen recipe.
- Standalone machine-pair rows have `human_reviewed: false`. Their immutable pair provenance keeps that value even after the pair is included in a reviewed release.
- Human-adjudicated rows have `human_reviewed: true`, `label_origin: "human_adjudication"` and the full `adjudication` record. This identifies the label's source; it does not mean the detector agreed or a separate independent ground-truth study occurred.
- All reviewed release rows have `human_reviewed: true`, the frozen ordinary `review` record, and `release_id`. For machine pairs, this means a human approved the recorded machine evidence. For adjudicated pairs, it records the later release-eligibility approval. The software requires a separate decision, not necessarily a different human reviewer. **Neither flag establishes independent ground truth.**
- Replay rows remain `evidence_source: "fixture_replay"`. The reader rejects them unless `allow_replay=True` is passed explicitly. Never mix these illustrations into real accuracy measurements.

Each frame's detector confidence and boxes are observations that can be wrong. Archive validation reproduces the exact recorded version's relation/control decisions from saved observations and, where applicable, original-image registration/appearance evidence. It never reruns the vision model. Matching the saved result establishes reproducibility, not perceptual truth. Real collection accuracy requires independent labels over complete capture histories; released pairs alone cannot estimate rejection coverage or recall.

## Human authority and immutable decisions

A human pair preserves both original captures verbatim, including their original roles, refusals and any `result.accepted: false` values. Its `evidence_adjudication` binds the assigned pair roles, human answers, reviewed controls, reason, reviewer, timestamp and hashes of the exact source evidence. The consumer reproduces the original machine decisions and validates that binding before accepting the human-origin rows. It never replaces a refusal with a fabricated machine success.

Adjudication can resolve eligible perceptual uncertainty after inspecting all recorded evidence. It cannot waive a known false control, malformed observation, interrupted sequence, unusable temporal ordering or missing original media. Independent geometry checks prevent an early uncertain detection from hiding another control that visibly failed. Detector boxes remain predictions; a human relation label does not correct those boxes.

An optional `baseline_authorization` lets a collector continue from an uncertain v2 first observation only when its role geometry already passes and the remaining uncertainty is eligible for review. This authorization is separate from both machine acceptance and pair adjudication. The manifest carries its binding to the original recipe, execution contract and baseline; the counterpart retains the exact authorization it used. `human_review_required` and any `recovery_unresolved` flag remain visible. Ordinary machine publication refuses this path: the pair needs explicit human adjudication, then ordinary approval before a release can include it. See [the evidence lifecycle](EVIDENCE_REVISIONS.md).

Re-verification appends a separate evidence revision with its requested verifier version and source bindings. It compares saved evidence without making new detector calls. The original pair and already frozen releases retain their original decisions and reviews; the newest comparison is not silently substituted into their JSONL. Full operator revision/disposition history is a separate audit record, not an additional member automatically embedded in every release ZIP.

## Identity and reproducibility

The frozen recipe snapshot is validated against its exact registered version and digest, including roles, thresholds, capture policy, and limitations. Missing legacy snapshots, unknown versions, or modified thresholds fail closed; the reader does not quietly reinterpret old data using current defaults.

New pairs also carry an `execution_contract` frozen before collection. It identifies the provider, versioned model route, endpoint, adapter/normalizer versions and request settings. The consumer checks recorded provider/cache observations against it. Older archives may lack this optional field; accepting their historical schema does not establish that their execution was pinned. Explicit migration does not rewrite those old artifacts. This contract identifies request semantics, not a cryptographic digest of remote model weights. See [execution contracts](plans/EXECUTION_CONTRACT.md).

A release's `content_sha256` hashes compact canonical JSON:

```python
payload = {
    key: value for key, value in manifest.items()
    if key not in {"id", "created_at", "content_sha256"}
}
canonical = json.dumps(
    payload, sort_keys=True, separators=(",", ":"),
    ensure_ascii=True, allow_nan=False,
).encode("utf-8")
digest = hashlib.sha256(canonical).hexdigest()
```

Thus a new release ID or creation time can describe identical content, but changed membership, recipe, campaign, pair evidence, or frozen review changes the content digest. This is an integrity and reproducibility mechanism, **not a digital signature**. Someone who controls all archive contents can rewrite observations and recompute hashes. Verify the source through your normal distribution/authentication channel.

Partitions use the SHA-256 of the frozen `split_group` ID modulo 100: buckets below 80 are `train`, below 90 are `valid`, and the rest are `test`. An independent session uses its own ID. Sessions deliberately assigned to the same operator scene group use the same opaque group UUID, even across different collectors. `split_provenance` preserves the group's ID, kind and display name; matching names alone never merge groups. Historical pairs without group provenance retain the original session-only rule. These buckets define a deterministic policy, not a guarantee of exact 80/10/10 proportions in a small release.

The reader validates the group provenance and recomputes each partition, rejects repeated pair/session/capture identities, and refuses exact same media bytes in different partitions. Same-partition reuse is allowed. Explicit grouping prevents known related sessions from being split apart; it does not discover undeclared scene relationships, physical object reuse or near-duplicates. A downstream data loader must preserve the group rather than randomly repartition individual images.

The local archive freezes Counterpart's original evidence and review. Later Roboflow annotation snapshots and hosted-version receipts are separate delivery records; they do not replace local images, original detector observations or this archive's release digest. An imported hosted COCO document verifies its declared membership, partitions and boxes against the approved snapshot, not its download origin or the remote image bytes. See [Roboflow delivery](ROBOFLOW_DELIVERY.md).

## Admission limits and failure behavior

Defaults admit archives up to 2 GiB on disk, 4 GiB expanded, 100,000 ZIP entries, 10,000 pairs, 32 MiB per metadata file and central directory, 512 KiB per JSONL line, and 2 MiB per image. Images retain the capture service's 80–2560 pixel and four-megapixel limits. Limits are explicit in `ConsumerLimits` and can be raised deliberately for trusted large releases. Prefer smaller releases to unbounded memory allocation. The writer currently uses ZIP_STORED; ordinary DEFLATE is accepted with an expanded/compressed ratio limit of 100.

The reader bounds the central directory before constructing Python's `ZipFile`, supports bounded ZIP64 directories, and rejects multi-volume/encrypted archives, executable prefixes, unsupported compression, symlinks, paths outside the exact schema, duplicate entries, unexpected members, missing assets, CRC errors, hash/size/dimension mismatches, nonfinite JSON, duplicate JSON keys, unsupported recipes, and inconsistent answers or reviews. ZIP files are never extracted. An error raises `ArchiveValidationError`; the inspection command exits 2 without continuing a partial dataset.

Validation bounds image sizes and archive metadata. Visual recipes also read the original pixels needed to reproduce their checks; shared reference images may be read again. Both the ZIP directory and manifest are bounded, resident structures, so the reader does not claim constant total metadata memory. JSONL must match the canonical rows in pair/member order exactly, including frozen review and provenance; extra, missing, reordered, or changed rows are rejected.
