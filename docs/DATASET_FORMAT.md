# Portable Counterpart datasets

A dataset release is a ZIP with `manifest.json`, `examples.jsonl`, and original images at `assets/<sha256>`. Both members of a pair stay together. Temporal examples contain the original sampled frames in timestamp order, not an invented video or guessed event time. Assets are deduplicated by exact bytes.

A standalone pair download has schema `counterpart.pair.v1`. A reviewed collection release has schema `counterpart.dataset.v1` and contains the selected immutable pair manifests plus the exact approved review version for each pair. Later reviews do not rewrite an earlier release.

## Use the data immediately

Validate a downloaded release without a database, network request, or model:

```sh
uv run python scripts/inspect_release.py /path/to/release.zip
# An illustrative replay download requires explicit opt-in:
uv run python scripts/inspect_release.py /path/to/replay.zip --allow-replay
```

The reader validates the complete archive before yielding any examples. Nothing is extracted to the filesystem. Standard Python is enough to iterate rows; Counterpart's existing Pillow dependency verifies the original image encoding. No PyTorch dependency, inference, or training is involved.

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

- Spatial answers come from the recorded left/right relation. They are not inferred solely from what the collector was asked to do.
- Temporal answers name the first object whose observed arrival interval establishes entry into the target band. The question preserves the frozen 62%-of-image-width band definition.
- Standalone pair rows have `human_reviewed: false`.
- Reviewed release rows have `human_reviewed: true`, the frozen `review` record, and `release_id`. This means a human accepted the recorded machine evidence. **It does not mean the label is independent human ground truth.** The immutable pair provenance remains machine evidence, with `human_reviewed: false`, even inside that release.
- Replay rows remain `evidence_source: "fixture_replay"`. The reader rejects them unless `allow_replay=True` is passed explicitly. Never mix these illustrations into real accuracy measurements.

Each frame's detector confidence and boxes are observations that can be wrong. Archive validation reruns the versioned deterministic relation/control checks over those recorded boxes. It never reruns the vision model and cannot prove what is actually visible in the pixels. Real collection accuracy requires independent labels over complete capture histories; released pairs alone cannot estimate rejection coverage or recall.

## Identity and reproducibility

The frozen recipe snapshot is validated against its exact registered version and digest, including roles, thresholds, capture policy, and limitations. Missing legacy snapshots, unknown versions, or modified thresholds fail closed; the reader does not quietly reinterpret old data using current defaults.

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

Session splits use the SHA-256 of `session_id` modulo 100: buckets below 80 are `train`, below 90 are `valid`, and the rest are `test`. A pair's `split_group` is its collection session. The reader rejects changed splits, repeated pair/session/capture identities, and exact same media bytes appearing in different partitions. Same-partition reuse is allowed. These checks prevent exact-byte leakage; they do not detect similar scenes, repeated physical objects, near-duplicates, or every source of evaluation leakage.

## Admission limits and failure behavior

Defaults admit archives up to 2 GiB on disk, 4 GiB expanded, 100,000 ZIP entries, 10,000 pairs, 32 MiB per metadata file and central directory, 512 KiB per JSONL line, and 2 MiB per image. Images retain the capture service's 80–2560 pixel and four-megapixel limits. Limits are explicit in `ConsumerLimits` and can be raised deliberately for trusted large releases. Prefer smaller releases to unbounded memory allocation. The writer currently uses ZIP_STORED; ordinary DEFLATE is accepted with an expanded/compressed ratio limit of 100.

The reader bounds the central directory before constructing Python's `ZipFile`, supports bounded ZIP64 directories, and rejects multi-volume/encrypted archives, executable prefixes, unsupported compression, symlinks, paths outside the exact schema, duplicate entries, unexpected members, missing assets, CRC errors, hash/size/dimension mismatches, nonfinite JSON, duplicate JSON keys, unsupported recipes, and inconsistent answers or reviews. ZIP files are never extracted. An error raises `ArchiveValidationError`; the inspection command exits 2 without continuing a partial dataset.

Validation reads each original image once and retains only bounded metadata plus one decoded image at a time. It does not claim constant total metadata memory: both the ZIP directory and manifest are bounded, resident structures. JSONL must match the canonical rows in pair/member order exactly, including frozen review and provenance; extra, missing, reordered, or changed rows are rejected.
