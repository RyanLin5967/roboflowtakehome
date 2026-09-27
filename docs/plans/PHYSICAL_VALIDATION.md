# Physical validation protocol

Protocol: `counterpart.physical-study.v1`, frozen before any desk-scene evaluation on 2026-09-26. An engineering test suite does not establish camera accuracy. Real Roboflow transport checks, public-photo diagnostics, independent human-reviewed collection, and infrastructure fault replay are separate evidence categories.

## What this evaluation establishes

Counterpart's claim is selective acceptance of a requested spatial/temporal change while checking named controls, with repair while the scene is available. We must measure the correctness of accepted captures **and how often usable captures are refused**. Reporting only the downloadable accepted pairs hides that tradeoff. A detector's confidence is not ground truth; passing our own threshold checks cannot independently validate those checks.

The primary physical metrics are:

- Accepted precision: independently valid, answer-correct accepted attempts / all accepted attempts with decisive human review.
- Valid-capture recall: accepted, independently valid, answer-correct attempts / all independently valid attempts. This requires rejected attempts too.
- Acceptance rate: accepted attempts / reviewed evaluation attempts. Also report the all-submission operational denominator, separately from holdout metrics.
- Human uncertainty and accepted-but-human-uncertain counts. An unclear human judgment is never silently converted into correct, incorrect, or a discarded record.
- Pair precision: both published members independently valid and answer-correct / all decisively reviewed published pairs. Pair publication is not an independent unit when several retries belong to the same scene.
- Breakdown by recipe, physical scene, session and challenge condition; code histograms identify the refusal mechanism.

Numerators and denominators accompany every rate. There is no promised percentage or post-hoc minimum needed to make the presentation look good.

## Freeze, collect, then review

1. Preserve the recipe snapshot, verifier version, provider/model ID, current thresholds, capture app commit and this protocol with the study. Save all original captures and rejected attempts. Do not mutate a frozen study after observing failure cases.
2. Assign physical `scene_id` before capture. Restarting a browser, creating another session, changing an attempt, or taking more frames does not create a new independent scene. Use the same scene group for closely related lighting/position variations in one setup. A one-desk pilot can appropriately have just one cluster.
3. Keep calibration scenes separate from evaluation scenes. Tune only on calibration. The evaluator rejects the same scene or identical media appearing in both partitions. A model's exposure to public training data remains a separate unknown; no claim of an unseen benchmark follows from these checks.
4. Capture the full shot list, including unsuccessful attempts. Stop for real budget/provider limits; record unfinished work. Never omit a difficult scene after seeing its detections. Submitting previews is optional during the accuracy study and must have a separately bounded allowance.
5. A reviewer sees original images/sequence, timestamps, the requested recipe and reference capture, without model boxes, decision, confidence or repair text. They record validity, observed answer and uncertainty. A second reviewer adjudicates disagreements where available; unresolved cases stay uncertain. Independent review here means independent of model output, not an invented claim that a professional annotation team participated.
6. Export complete immutable session audits and separate human-label JSONL, then run the offline evaluator. Keep calibration labels in the input for leakage checks; the report excludes them from evaluation metrics.

## Physical shot list

Use an ordinary cup and bottle first. Other supported class pairs are separate strata, not a replacement chosen after one fails. Record daylight/dim conditions, clutter, camera height and prop appearance without collecting unnecessary personal information. The protocol is a pilot, not a representative survey of all users or objects.

| Recipe | Attempt after a saved reference | Independent judgment target |
|---|---|---|
| Spatial | Clear requested side swap; anchor and camera stable | Valid if image-plane separation and named controls genuinely hold |
| Spatial | Keep the first relation instead of swapping | Invalid requested relation |
| Spatial | Swap but visibly move the anchor | Invalid control |
| Spatial | Swap but change camera distance/angle | Invalid if recorded named position/scale controls no longer hold; no claim of full camera-pose proof |
| Spatial | Add a second instance or partially hide a prop | Mark visible identity/ambiguity from original pixels; do not label from detector behavior |
| Spatial | Borderline separation or poor lighting | Valid, invalid or uncertain by independent image review; challenge type alone is not its label |
| Temporal | Two stable endpoints with clearly separated opposite arrivals | Valid only if sampled evidence establishes order and matching endpoints/lanes |
| Temporal | Repeat the first order | Invalid requested order |
| Temporal | Nearly simultaneous arrival | Uncertain when capture samples cannot distinguish the intervals, even if the operator remembers the true action |
| Temporal | Leave the target, return, cross lanes, or move camera | Invalid relevant control where visible |
| Temporal | Interrupt/background recording or leave a long sampling gap | Evidence insufficient; do not reconstruct an unobserved event from instructions |
| Temporal | Same final image but opposite order | Verify using the full sequence, never by comparing final frames alone |

For temporal review, the question is arrival into the recorded target band, **not motion onset**. Use the frozen recipe's band and sampling policy. Hand occlusion may cause a valid action to be unusable evidence; report this as a collection failure rather than fabricating detections.

A sensible first pilot is one physical setup with every listed failure mode. A stronger holdout adds independent locations/prop sets and operators. Eight independent scene groups are the minimum for the evaluator to even attempt cluster intervals, not a guarantee of adequate power. If only one operator/desk is available, show the exact pilot counts and recorded failures instead of a universal accuracy claim.

## Useful baseline, not a strawman

**Written shot list + competent post-hoc review** is the baseline. Give the same recipe instructions, example framing and timing guidance; allow ordinary photo/video capture and a human reviewing both members. Do not compare against random labels, no instructions, or a deliberately incompetent reviewer.

There are two different experiments:

1. **Filtering quality on identical evidence:** a separate post-hoc reviewer judges exactly the same frozen captures, blinded to Counterpart and the independent ground-truth reviewer. Supply these actual decisions through `--baseline-decisions`. This compares accepted precision/refusal on matched evidence. It does not establish that live guidance made collection faster or improved the originally captured scenes.
2. **Collection value:** collect both workflows on the same predeclared scene configurations with randomized/counterbalanced workflow order. Record time from reading the task through accepted reviewed pair, active collector time, reviewer time, all retakes, abandoned tasks, cloud calls and completion rate. Use equivalent fault challenges, such as a visibly moved anchor. Restore the scene between conditions. Reusing one recording with a simulated faster timer is not this experiment. Report within-scene paired differences; with one collector this is an explicitly limited pilot.

The current evaluator implements experiment 1. No timing/efficiency improvement is emitted without actual experiment-2 observations. In a presentation, a side-by-side real repair demonstration is useful qualitative evidence even before a sufficiently powered time study exists.

## Saved input contracts

A normal `counterpart.pair.v1` manifest works for **published-only precision**. Coverage and recall are suppressed because missing rejects cannot be reconstructed.

For the complete sampling frame, use a session audit downloaded from the app/API containing:

```json
{
  "schema_version": "counterpart.evaluation.session.v1",
  "session_id": "original-session-id",
  "recipe": "spatial",
  "mode": "live",
  "captures_complete": true,
  "provenance": {"evidence_kind": "roboflow_capture"},
  "recipe_snapshot": {"object_a": "bottle", "object_b": "cup"},
  "captures": [],
  "pairs": []
}
```

`captures` must contain the full chronological committed history, not just the latest baseline/counterpart pointers. Existing capture records retain IDs, status, slot, original frame hashes/times, result and per-frame model provenance. Preview/recovery records may be included; the evaluator excludes them. Queued/running attempts remain explicitly counted in population status totals but are not independently judged before they finish. Superseded/failed attempts remain in finished submission counts. `pairs` contains any complete published manifests.

Create a blank reviewer file with the saved input paths:

```sh
uv run python scripts/evaluate_captures.py artifacts/study/session-01.json \
  --write-label-template artifacts/study/human-labels.jsonl
```

This emits no guessed labels and no model answer. For each row fill `scene_id`, `condition`, `reviewer_id`, `reviewed_without_model_output: true`, `decision: valid|invalid|uncertain`, and an independently observed `observed_answer` (`left|right` for spatial; the actual class name for temporal; null when unresolvable). Preserve capture/session IDs and both `evidence_digest` and `recipe_digest`; these bind the label to original media hashes, ordered timestamps, slot and frozen recipe instructions. A valid decision requires a resolved answer. Set `partition` to `calibration` or `evaluation` according to the predeclared split.

Review validity of counterparts **against their original reference**, not just the requested answer. The template includes original reference asset IDs and a bound reference digest. Historical rejected attempts without a persisted reference cannot be judged valid; uncertainty is retained rather than reconstructing a reference from the latest session pointer. Keep the original pair/scene materials available to the reviewer; the template's asset references are a review index, not a substitute for opening those images.

```sh
uv run python scripts/evaluate_captures.py artifacts/study/session-01.json \
  artifacts/study/session-02.json --labels artifacts/study/human-labels.jsonl \
  --output artifacts/study/report.json
```

Optional matched baseline JSONL rows use `schema_version: counterpart.posthoc-decision.v1`, `capture_id`, `evidence_digest`, `recipe_digest`, `reference_capture_id`, `reference_evidence_digest`, distinct `reviewer_id`, `reviewed_without_model_output: true`, `decision: accepted|rejected|uncertain`, and the baseline's own `answer`. Supply exactly the evaluation capture IDs using `--baseline-decisions`. Missing/extra judgments or using the ground-truth reviewer as the baseline fail rather than silently shrink the comparison.

Reports hash every input and the human/baseline judgment files. Output creation is exclusive: existing reports/labels are never overwritten. Repeated manifests are deduplicated by immutable ID; conflicting snapshots fail.

## Confidence and failure handling

The optional 95% interval resamples whole scenes, with a fixed seed and 1,000 resamples. It does not treat frames or retries as independent. Fewer than eight independent scenes, too many undefined resamples, or degenerate all-success/all-failure results produce an explicit unavailable interval. An all-correct tiny pilot does not justify a zero-width perfect-accuracy interval. These are exploratory intervals, not a formal certification.

Missing human labels and review coverage are printed. Headline metrics are conditional on the reviewed evaluation subset; do not generalize them when review is selective. The all-input operational acceptance denominator is reported separately and explicitly includes calibration where present.

Provider faults, stale-job rejection and reconnection are reliability metrics, not mistaken object predictions. Preserve their codes and measure them separately. A physical failure discovered after a frozen report starts a new calibration revision and a new untouched evaluation set; it never retroactively changes the original answer.

## Budget and evidence status

No cloud work is started by this evaluator. Root coordinates any provider calls through the durable ledger, whose current ceiling is 512 total image reservations; consumed/ambiguous attempts count. Public photo diagnostics need only a small preselected subset. Desk temporal sequences multiply the image budget quickly: three 12-frame attempts use 36 calls before previews. Reserve physical evaluation and retries before spending on a broad public benchmark.

At creation of this protocol, no independent human desk-capture labels have been supplied. The 28 evaluator tests validate arithmetic, provenance gates and refusal of misleading input; they are not CV performance results. Public dataset labels are not human collection-efficiency evidence. See [physical_sources.md](../../research/physical_sources.md) for external-data suitability and limitations.
