# Counterpart web

Camera-first React/TypeScript client. All model work happens on the server. Session capabilities stay in local browser storage and request headers; provider credentials never enter this bundle.

## Run

```sh
npm ci
npm run dev -- --port 5173
```

Vite proxies `/api` to `http://127.0.0.1:8080`. Set `COUNTERPART_API_URL` to override the development proxy. Production `npm run build` creates `dist/`, which the API can serve on the same origin.

Camera capture requires HTTPS or localhost. An ordinary HTTP LAN address does not satisfy phone camera security requirements. No microphone is requested.

## Verification

```sh
npm run build
npx playwright install chromium
npm test
```

Browser tests intercept every API route and use Chromium's generated fake camera. They verify collection/recovery/publication UI, temporal sampling, background interruption, camera denial, mobile layout, and immutable retry identity across reload. They are not model quality tests, provider integration tests, or a physical phone-camera validation.

## Important behavior

- Explicit replay mode is labeled as simulation, including completed artifacts.
- Preview hints are optional, limited to 12 requests per opened client session, and cannot accept a committed capture. The durable backend budget is authoritative.
- Canonical temporal evidence consists of timestamped JPEG frames sampled at a target 4fps for at most 12 seconds; sequence playback follows recorded deltas. Hidden-page, missing-frame and orientation interruptions invalidate the attempt.
- Accepted first captures remain on the server. Unconfirmed committed blobs and their exact idempotency identity are retained in IndexedDB for retry. A stale epoch conflict requires an explicit discard/retake; metadata is never silently rewritten.
- Saved images are prefetched, decoded and cached by immutable asset identity. Sequence playback keeps the previous decoded frame until the next is available.
- Resuming restores digital work and requires the server's physical scene check for incomplete pairs.

The browser is not a trusted evidence authority. The server validates bytes, transitions, recipe checks, and publication.
