# Verification record — 2026-09-26

| Layer | Executed result | What it establishes |
| --- | --- | --- |
| Python/database/provider-contract suite |254 passed, no skips|Rules, actual DB transactions/races, interrupted subprocess recovery, budgets, capabilities, HTTP adapters and export state-machine faults|
| Browser suite |9 passed|UI, native generated-camera sampling, interruption, persistent retry identity; APIs mocked|
| Full-stack browser |Spatial and temporal walkthroughs passed|Production UI, actual API/worker/Postgres, synthetic observations; repair, reload recovery, publication and ZIP|
| Build/lint |TypeScript/Vite, Ruff, wheel passed|Buildable source; SQL schema in wheel|
| Hosted RF-DETR |One failed wrong-route request, one corrected success|Key, route/auth/schema compatibility on traffic photo; not cup/bottle quality|
| Dataset transport |Generated card upload, empty prediction acknowledgement, eventual metadata reconciliation|Real account write/search behavior in public test-only project; not a real collected pair|
| Infrastructure replay |240 previews→12 pending→12 latest served; orphan lease recovered, old result refused|Bounded burst coalescing/recovery without provider calls; not sustained capacity or collector efficiency|

The single successful inference took7.349s including network/startup effects; not a latency benchmark. The local replay measured median claim27.963ms/max40.873ms under its tiny stated workload. Its12 sessions are synthetic traffic.

One upstream Starlette warning concerns future HTTPX test-client migration. Browser tooling emits NO_COLOR/FORCE_COLOR notices. No failing tests were omitted.

With API+worker running:

```
node scripts/browser_smoke.mjs
uv run python scripts/reliability_probe.py
```

`scripts/provider_smoke.py` invokes one real hosted inference only with live mode and a durable allowance configured; excluded from CI. Default automated tests never call models. Raw screenshots/probe reports are ignored under artifacts/; this summary is durable documentation. CI is configured, not yet executed remotely.

Missing: physical collector trials, threshold calibration on desk scenes, measured CV accuracy, real temporal quality, human-reviewed labels, real camera-pair export and public deployment validation. Separate gates from software/replay correctness.
