# Feedback scheduling benchmark

This measures the real PostgreSQL capture scheduler under a controlled stream of disposable previews and durable captures. It uses generated observations and timed sleeps in place of model calls. It measures scheduling behavior, **not Roboflow latency, perception accuracy, browser rendering speed or production capacity**.

The original proposal required capture-to-feedback age at increasing offered load, compared with a competent bounded queue. Earlier probes established burst coalescing and failure recovery but only timed submission. This benchmark closes that distinct measurement gap.

## Matched controls

All three initial policies use the production `Repository.submit`, `claim`, `finish` and `snapshot` paths against a temporary PostgreSQL schema. The comparator changes only the pending sampling rule and the session selection order. It fails closed if the production SQL changes without a comparator review.

| Policy | Pending preview | Dispatch between eligible sessions |
|---|---|---|
| Counterpart | Keep the newest | Least recently served |
| Latest + FIFO | Keep the newest | Oldest pending work first |
| Sampled + FIFO | Keep the first; sample out arrivals while one is pending | Oldest pending work first |

Latest + FIFO already has the freshness optimization, but its replacement creates a new pending job and resets the queue position. Independent review identified that as a significant limitation: frequent replacements can repeatedly move one collector behind everyone else. The original result is preserved and a separately tested **stable-position latest FIFO** control retains the pending queue slot's age while replacing only its frame. The capture timestamp, expiry and in-flight lease are unchanged. Sampled + FIFO is also bounded, preserves useful in-flight results, prioritizes committed work and has only one pending preview per session. No control has an unbounded obsolete-frame queue.

Every policy has the same two worker threads, global admission limit of 128 active jobs, production running-lease limit of four, one in-flight and one pending preview per session, ten-second preview expiry, saved-capture priority, snapshot reader, seeded offered schedule and per-frame synthetic service delay. Capture-lease heartbeats, the remote model cache and cloud budget reservations are outside this scheduler benchmark.

The main matrix uses four preview collectors, two separate committed captures, five cases and two repeats. Preview rates are 6, 16 and 60 frames/second in total, with 100 ms synthetic service (seeded ±20% per offered frame). Another 60/s case gives one collector six times the others' arrival rate. A 16/s sensitivity doubles service delay to 200 ms. Each main trial offers for three seconds and drains for at most ten more. Six additional trials compare all three policies at 16/s and 60/s using the UI's 1.5 second polling cadence over 4.5 second offered horizons. These are intentionally short, bounded checks; per-session rates stress the scheduler and do not mimic the current UI's 0.5 Hz preview cadence.

Order is independently shuffled within each case/repeat. Policies receive the same planned arrival offsets, frame-specific delays and offered-work SHA for that case/repeat. Actual arrival emission, driver waiting and realized sleep timings are measured, not assumed. The emitter timestamps each offer with `perf_counter` before enqueueing it; DB backpressure cannot silently shift the capture time forward. The driver itself is bounded to 128 queued offers and records dropped offers separately.

## Metrics and denominators

- **Capture → persisted feedback:** original monotonic capture time through actual committed verification result. Fenced/stale results are excluded and counted separately.
- **Capture → client-observed feedback:** original capture time through a real `Repository.snapshot` read that returns the result. Main cases poll every 100 ms plus query time to separate scheduler effects. The initial UI-cadence cases poll every 1.5 seconds, matching the browser's idle cadence at that checkpoint. Neither includes HTTP, image upload or rendering; no recorded number is presented as rendered end-user latency.
- **Age of the last observed answer:** integrate the age of the last result actually seen across each collector's fixed offered horizon. Report no-feedback time before the first answer separately; do not assign a convenient zero to starvation. A result only seen after the horizon does not erase that no-feedback interval.
- **Admission and work:** offered, admitted, intentionally sampled, capacity-rejected, driver-dropped, superseded, completed and stale outcomes; actual synthetic service calls; sampled queue depth; committed capture completion.
- **Fairness:** per-client offers, service, visible updates, no-feedback time, maximum update gaps and served/offered fraction. Jain's index describes parity of work counts, with both offered-horizon and final-drain counts. Under unequal demand, lower work parity alone is not proof of unfairness.

P95 is linearly interpolated from the samples that exist. It conditions on completed/observed answers; per-client coverage metrics expose missing answers. Three-second trials have few tail samples. Preserve every trial; do not use a best run, pool away adverse runs, claim significance or manufacture a scheduler win if the strong control performs similarly.

## Reproduce

```sh
COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest tests/test_feedback_benchmark.py -q
uv run python scripts/feedback_benchmark.py --duration 3 --repeats 2 --out research/feedback_benchmark.json
```

The script creates and drops only random `feedback_*` schemas. It does not use credentials, start ML, touch the app's development schema or make network model calls. Keep other DB tests idle during timing measurements. Source revision, loaded repository source hash, benchmark hash, OS/Python/PostgreSQL, per-trial order, raw offered/service/visible events and policy bounds are saved in the JSON. A changed repository file during measurement is explicitly flagged.

## Executed initial matrix

[The complete original artifact](../research/feedback_benchmark.json) records all 36 trials. The source tree stayed unchanged during the timed run; other project DB tests, browser work and VM/build work were paused. The existing API/worker remained idle. Host: Mac17,8, arm64, 18 logical CPUs, 48 GiB RAM, Python 3.12.13 and PostgreSQL 17.11. This was a shared development machine, without CPU affinity or production-scale network isolation.

There were **3,945 offers**, **3,204 admitted captures**, **741 intentional sampling drops**, **1,481 pending previews superseded**, and **1,723 synthetic service calls**. All **72/72 committed captures** persisted and were observed. All queues drained. There were no capacity rejections, driver drops or stale completions in this workload. The sampler saw at most seven active jobs. Maximum arrival-emitter lateness was 5.6 ms; maximum admitted driver waiting was 34 ms. These counts are conserved in the raw artifact.

The table reports **p95 capture → server-available feedback, milliseconds**. Each cell contains the two individual trial values, rounded; it does not select the better run.

| Case | Counterpart | Latest FIFO, resetting queue position | Sampled FIFO |
|---|---:|---:|---:|
| 6 previews/s | 148 / 150 | 143 / 145 | 146 / 149 |
| 16 previews/s | 329 / 271 | 262 / 259 | 254 / 268 |
| 60 previews/s | 192 / 192 | 211 / 215 | 410 / 386 |
| 60/s, one noisy collector | 282 / 256 | 281 / 280 | 367 / 385 |
| 16/s, doubled service time | 475 / 430 | 473 / 512 | 683 / 753 |

Counterpart was **not consistently faster near capacity**. In the overload case its 100 ms reader p95 was 299 / 299 ms versus 297 / 299 ms for latest FIFO: effectively similar in these small trials. The sampled control processed older previews, which is expected from keeping the first pending frame; no completed result exceeded the actual stale-result limit in these trials.

The original latest FIFO showed a worse collector-level failure: in both noisy-client trials the frequent sender started only 2 of its 120 offered previews during the offered horizon, with a 2.54–2.55 second feedback gap. Counterpart started 12–13 previews per collector and kept gaps below 379 ms. **This is not sufficient evidence that fair scheduling beats a competent stable-position FIFO**; resetting queue position explains the original control's starvation. That criticism caused the follow-up below.

The original 1.5 second reader exposed a separate product bottleneck. With Counterpart, server p95 was 285 ms at 16/s and 209 ms at 60/s, but observed-answer p95 was 1,594 ms and 1,495 ms. The client observation interval dominated those results. This prompted adaptive browser polling: 250 ms while a locally submitted preview is awaiting feedback, 1,500 ms after matching terminal feedback or a four-second timeout, and the existing 650 ms for pending saved captures. New preview submission wakes idle polling within 250 ms; requests never overlap. Camera/guidance/visibility cleanup stops fast polling. Browser tests cover those transitions separately.

## Reviewed follow-up

`scripts/feedback_followup.py` preserves the original benchmark script and 36-trial artifact, links them by SHA, and reuses the exact offered schedules and per-frame delays. Eight additional trials compare Counterpart against stable-position latest FIFO in overload/noisy cases with two repeats. Six more use a 250 ms reader for the active-preview condition at 16/s and 60/s. The fixed 250 ms sensitivity does **not** emulate the entire adaptive browser state machine, HTTP or rendering; the browser interaction tests establish that policy separately.

The stable comparator holds the production session lock while replacing a pending frame, preserves only the pending job's queue timestamp, and starts a new queue age after that slot is claimed. It adds one indexed pending lookup and one timestamp update per replacement; those costs are included. Tests verify slot ordering, fresh capture timestamps, reset after dispatch, unchanged pending bounds and saved-capture priority.

```sh
uv run python scripts/feedback_followup.py --out research/feedback_followup.json
```

[The follow-up artifact](../research/feedback_followup.json) records all 14 additional trials, also executed with the source unchanged and competing DB/browser/build work paused. Its 2,491 offers produced 2,298 admitted captures, 193 intentional sampling drops, 1,397 superseded previews and 901 synthetic service calls. All **28/28 committed captures** persisted and were observed; there were no capacity rejections or driver drops, and every queue drained.

The stronger control removed the earlier starvation. **These results do not establish a general latency advantage over competent stable-position FIFO.**

| Case | Counterpart server p95, ms | Stable-position FIFO server p95, ms | Counterpart worst feedback gap, ms | Stable FIFO worst feedback gap, ms |
|---|---:|---:|---:|---:|
| Overload, trial 1 | 219 | 197 | 365 | 412 |
| Overload, trial 2 | 191 | 198 | 369 | 381 |
| Noisy collector, trial 1 | 274 | 276 | 376 | 370 |
| Noisy collector, trial 2 | 270 | 252 | 376 | 484 |

Under the noisy workload, Counterpart started 12–13 previews per collector during each horizon; stable FIFO started 10–15. Both delivered regular feedback. Counterpart's explicit service ordering is useful and passed this workload, but stable FIFO is a valid simpler comparator. The earlier two-second starvation finding belongs to the queue-position-resetting ablation, not to FIFO as a category.

The active-preview polling sensitivity produced a clear separate improvement in the observation path:

| Reader condition | Counterpart observed-answer p95 at 16/s | Counterpart observed-answer p95 at 60/s |
|---|---:|---:|
| Original idle-style 1,500 ms interval | 1,594 ms | 1,495 ms |
| Active-preview 250 ms interval | 475 ms | 432 ms |

These are fixed-cadence **server snapshot reader** measurements using the same offered schedules and frame-specific synthetic delays, collected in separate quiet-window runs. They are not real model or rendered-browser latency promises. Each policy's full new results, including the faster FIFO results in some cases, remain in the JSON. The browser's actual pending/wake/idle/timeout behavior has separate interaction tests; the sensitivity does not model that entire state machine.

Independent review recomputed percentiles, current-result filtering, per-client age/no-feedback integrals, count conservation and matched schedule identities from both raw artifacts: **2,800 checks across 50 trials with zero discrepancies** (2,016 initial + 784 follow-up). Both timed source snapshots remained unchanged; recorded source/artifact hashes match. All **100/100 committed captures** across both matrices persisted and were observed. The stronger comparator's semantics also received independent source review. The focused benchmark suite has **18 passing tests**, including actual PostgreSQL trials for all original controls and the stronger comparator's ordering/freshness/bound contracts.

The defensible outcome is a bounded, durable scheduler with measured freshness and workload accounting, plus a product polling bottleneck found and fixed. It is not a new scheduling algorithm or a universal performance lead.
