# Public physical-data diagnostics

Investigated 2026-09-26 using the existing Exa helper and direct primary repositories/cards. Selection is for a small real-photo perception diagnostic; none of these datasets substitutes for collecting Counterpart sessions or independently checking its named controls.

## What's “up” — useful small photo probe

The official [code/data repository](https://github.com/amitakamath/whatsup_vlms) provides controlled spatial datasets and pair/set evaluation. Its [loader at commit 7c1f2550eace32e7b8c77de5a792347c402960d1](https://github.com/amitakamath/whatsup_vlms/blob/7c1f2550eace32e7b8c77de5a792347c402960d1/dataset_zoo/aro_datasets.py) identifies the original annotation/image downloads and specifies the first caption as correct. The [repository license](https://github.com/amitakamath/whatsup_vlms/blob/7c1f2550eace32e7b8c77de5a792347c402960d1/LICENSE) is MIT. No separate image-specific terms were found in those files; keep downloaded photos outside the repository/public demo and retain attribution rather than assuming unrelated source images have universal redistribution permission.

Read-only annotation checks found 412 Controlled-A and 408 Controlled-B rows. Controlled-B contains book/cup, mug/book and mug/phone left/right pairs compatible with supported object classes after explicitly mapping mug→cup and phone→cell phone. This mapping is a recorded ontology decision, not a new source annotation. The image archive HEAD reported 65,817,200 bytes. A six-image subset was selected before inference: both left/right examples for each of those three object pairs. No “best looking prediction” selection occurred.

Official download IDs in the pinned loader:

- Controlled-B annotations: `1unNNosLbdy9NDjgj4l8fsQP3WiAAGA6z`
- Controlled-B archive: `13jdBpg8t3NqW3jrL6FK8HO93vwsUjDxG`

Probe limitations: their left/right labels establish neither Counterpart's fixed-anchor requirement nor matching scale/camera geometry. Apply the per-image relation diagnostic separately from full pair admission; a legitimate rejection under our stricter controls is not automatically a wrong source relation. Do not claim cup/bottle validation: these selected source pairs have different objects. Detector pretraining exposure is unknown. File hashes/source IDs are saved with the local subset, not model-generated labels.

## VisMin — related but not the real-capture test

[VisMin's official repository](https://github.com/rabiulcste/vismin) documents minimal-change image pairs and generated edits. Its [published benchmark card](https://huggingface.co/datasets/mair-lab/vismin-bench) lists 2,084 examples and a CC-BY-4.0 dataset license. Useful reuse: pairwise answer evaluation and failure categories. Its edited-image pipeline is deliberately different from physical recapture, so high performance on these examples would not establish scene preservation or live repair. No images were downloaded for this build.

## Minimal Video Pairs — evaluation structure, not drop-in desk evidence

The [official MVP repository](https://github.com/facebookresearch/minimal_video_pairs) evaluates both members jointly; a pair only succeeds when both answers are right. It composes multiple original video sources and explicitly asks users to accept each source's terms. Its [repository license](https://github.com/facebookresearch/minimal_video_pairs/blob/main/LICENSE) is CC-BY-NC-4.0. Some sources require account/license steps. Reuse the paired evaluation principle and explicit source provenance; do not download a mixed video corpus as though it were one unrestricted source. Its actions/questions are not Counterpart's exact arrival-band recipe. No MVP videos were downloaded or run.

## Decision

Use the preselected six What's-up photos for a bounded hosted-detector diagnostic only if root reserves the calls. Evaluate actual spatial and temporal admission using preserved live Counterpart attempts plus separately provided human judgments, following [PHYSICAL_VALIDATION.md](../docs/plans/PHYSICAL_VALIDATION.md). Never import public or synthetic records into that physical study under fake capture provenance.

## Local subset evidence

The six originals are at `/tmp/counterpart-whatsup-smoke` (temporary local artifacts; not committed). All six were visually inspected before any inference. They are real tabletop photos; both objects change position across each left/right pair, so fixed-anchor pair acceptance is expected to fail. They remain useful per-image relation diagnostics and full-pair negative controls. Retained size: 989,944 bytes.

| Original filename | SHA256 |
|---|---|
| `book_left_of_cup.jpeg` | `0f1a76ed072600016caf50674bcfa8b56933d66f35cab9b7a838a964ff8fba73` |
| `book_right_of_cup.jpeg` | `57f5a20ddcf94c6188e8f943abe854c25863b409f0d142f3cefb28d81bde22bc` |
| `mug_left_of_book.jpeg` | `adb8bf97a53e22fb90bcbc7d9677ab107c811053308fecfe0db22fc3763b041f` |
| `mug_left_of_phone.jpeg` | `f287bbd11093d2fe73e0833188fa453475356a4f18627d3e656bf54bb2db48bf` |
| `mug_right_of_book.jpeg` | `57253d734b0869a37f8d5faa77275b51b02f2ca3b330d8de861630e8d41c28c2` |
| `mug_right_of_phone.jpeg` | `a3296ad0bd67d51e10250cb2ada5a9fabaaa5d7e1fe316225f07c005e2e65c82` |
