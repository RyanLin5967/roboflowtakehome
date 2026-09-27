# Counterpart web client

React/TypeScript client for a collector and the person running a collection. The same app serves both; permissions are enforced by the API.

## Run

From this directory:

```sh
npm ci
npm run dev
npm run build
npm test
```

The Vite development proxy targets the local API. Production assets are served by Counterpart from `dist/`. Camera capture needs localhost or HTTPS. A localhost invitation QR cannot reach a server from a different phone; use the HTTPS deployment origin when creating phone invitations.

## Product routes

- `#/capture` — spatial or temporal capture, replay exploration, scene recovery, durable uploads, recorded repair comparison and complete pair download.
- `#/workspace` — operator login or local loopback bootstrap, versioned collections, typed object roles, collection pause, collector invitations, review, dataset releases and explicit provider export status.
- `#/library` — the sessions opened by this browser, with their original access capabilities. Switching the active session does not discard other sessions or their pending uploads.
- `#/join/<secret>` — bounded collector invitation. The secret is a fragment, never an API URL/query parameter. It is submitted in a POST body. A saved join idempotency key survives a lost response; after a successful durable join, reopening the invitation can begin another session.

## Data and authority

Operator access uses a same-origin HttpOnly server cookie. The operator token is not persisted in browser storage. A collector has an individual session bearer capability. Browser IndexedDB holds session history and exact pending capture bytes; localStorage identifies only the currently active session. History has a 256-session admission limit and never silently evicts a capability. Forgetting access is explicit and refused while that session has an unconfirmed capture.

Live detections come from the server. The browser submits camera pixels and timestamps, never authoritative detections. Replay scenes are labeled test data. Custom-role instructions come from the persisted recipe snapshot; supported objects come from the server catalogue. Replay retains its actual bottle/cup fixture contract.

A complete captured pair, a human review, an immutable dataset release and a confirmed external export are separate states. Review mutations carry the version the reviewer saw. If a concurrent review arrives, the UI requires acknowledgement of the changed decision before another submission. Hiding the automated assessment removes expected-answer captions, recorded detections and machine checks; this switch by itself is not evidence of independent human validation.

## Source organization

- `App.tsx`, `camera.ts` — collector capture and recovery.
- `Evidence.tsx` — authenticated, cached original-frame rendering and timestamp-aware sequence playback.
- `Product.tsx`, `Shell.tsx` — routing, invitation entry and local session library.
- `Workspace.tsx`, `CampaignForm.tsx`, `CollectionDetail.tsx` — operator workflows.
- `PairReview.tsx`, `Invites.tsx` — original-evidence review and bounded share links.
- `api.ts`, `productApi.ts` — typed wire contracts and browser durability.
- `styles.css`, `product.css` — responsive capture and collection workspace styles.

QR generation reuses the MIT-licensed `qrcode` package, pinned to 1.5.4. QR contents use the actual current origin; no hosted QR service receives invitation secrets.

## Test boundary

Playwright tests exercise API protocol fixtures, camera browser behavior with generated input, protected media, mobile layout, review conflicts, release selection, capability storage and lost-response recovery. Test graphics and observations are explicitly synthetic; these tests do not measure computer-vision accuracy, actual cloud inference or real physical capture. Full API/worker/browser integration is tested separately by the repository's smoke scripts.
