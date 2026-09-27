# Original-scope closure loop

The user explicitly authorized fixing every gap in `docs/ORIGINAL_SCOPE_AUDIT.md`, with heavy independent review. The physical-camera trial remains deferred. The audit is the starting inventory, not proof that the following changes are complete. `BUILD_LOOP.json` and the latest execution record track actual progress.

## Closure requirements

| Lane | Required implementation and executed proof |
|---|---|
| Frozen execution | Collection/session binds the complete credential-free detector configuration; API and worker agree; configuration drift stops before spending; old evidence is migrated only from recorded provenance; manifest/consumer bind the contract. |
| Evidence revisions | New verifier decisions append against immutable source and reference hashes. CAS and idempotency handle repeated/concurrent requests. Prior decisions, human reviews and released artifacts remain intact. |
| Uncertainty review | Independently inspect saved original evidence. Resolve only supported perceptual uncertainty; malformed evidence and known-false controls cannot be approved away. Human-origin output stays distinguishable and requires normal dataset review. |
| Related-session partitions | Server-managed reusable scene groups fix a split before collection. Invite/session/group identity cannot be changed after admission. Releases and offline consumers enforce the group, including across campaigns and historical migration. |
| Scene controls | Explicit new recipe version with bounded conventional registration, appearance consistency and optional protected objects. Each clause has pass/fail/unknown semantics; insufficient evidence does not become success. Original byte hashes and measured transforms remain inspectable. No claim that pixels establish unique physical identity. |
| Feedback experiment | Actual production PostgreSQL queue under timed offered load, compared with bounded competent controls. Report all attempts, censorship/no-feedback, delivered feedback age and sample latency, fairness, committed work and work counts. Injected perception delay is labeled; preserve unfavorable results. |
| Roboflow delivery | Real supported annotation API, immutable corrected-annotation snapshots, explicitly reviewed delivery, exact source/membership/split checks, durable dataset-generation reservations and ambiguous-write reconciliation. Account entitlement remains separate from software availability. |
| Product integration | Every new operator action is reachable in the UI; capture/replay still works. Independent archive consumption handles new evidence schemas. Actual browser journeys, populated-schema upgrade, process faults and container HTTPS exercise integrated behavior. |

## Independent attacks

Reviewers own separate negative tests. Important attacks include stale model/configuration, forged observation provenance, missing-object uncertainty masking another known failure, unsupported verifier versions, concurrent human decisions, source/reference substitution, split-group mutation, cross-campaign leakage, external write acknowledgement loss and provider versions containing unrelated images. Passing implementation-owned tests alone does not close a lane.

## Boundaries retained

No local ML, paid upgrades or unbounded cloud work. Classical bounded pixel transforms and synthetic tests do not constitute model evaluation. Real physical quality and productivity claims require the later human trial. Do not export camera images to the current public-only Roboflow workspace. Public hosting and private-project entitlement cannot be invented by changing code. No remote push or publication has been requested for this repository.

## Integration ownership

Execution-contract agent owns core repository/worker/runtime/schema. Collection agent owns campaigns/invitations/splits/operator router. Vision agent owns recipes/verifier/classical checks/input parameters. Evidence agent owns revision/adjudication modules and consumer. Roboflow agent owns provider delivery modules. Frontend agent owns the browser. Root owns API mounting, configuration, lifecycle integration, CI and final evidence. Separate agents attack semantics, measure feedback and verify migrations/deployment. Avoid concurrent edits to another owner's files; send interface changes explicitly.
