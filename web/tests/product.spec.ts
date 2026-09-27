import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// Protocol and UI fixtures. These are deliberately not model-quality evidence.
const drawing = '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440"><rect width="720" height="440" fill="#e7e2d5"/><rect x="140" y="140" width="100" height="175" fill="#b57b5a"/><rect x="430" y="210" width="110" height="100" fill="#809275"/></svg>';
async function mockProduct(page: Page, options: { authenticated?: boolean; local?: boolean; empty?: boolean; replay?: boolean; reviewConflict?: boolean; failFirstJoin?: boolean } = {}) {
  let authenticated = options.authenticated ?? true;
  const posts: Array<{ path: string; body: any }> = [];
  const requestedUrls: string[] = [];
  const progress = { sessions: 1, published_pairs: 1, approved_pairs: 0, rejected_pairs: 0, needs_revision_pairs: 0, pending_review_pairs: 1, target_pairs: 12, remaining_pairs: 12 };
  const campaign: any = { id: 'campaign-0001', name: 'Objects, opposite stories', active: true, recipe: 'spatial', mode: options.replay ? 'replay' : 'live', object_a: options.replay ? 'bottle' : 'book', object_b: options.replay ? 'cup' : 'mouse', recipe_snapshot: {}, target_pairs: 12, created_at: '2026-09-26T12:00:00Z', progress };
  const capture = (slot: string): any => ({ id: `capture-${slot}`, slot, status: 'accepted', revision: slot === 'baseline' ? 1 : 2, epoch: 0, created_at: '2026-09-26T12:00:00Z', frames: [{ asset_id: `asset-${slot}`, width: 720, height: 440, t_ms: 0, detections: [{ label: campaign.object_a, confidence: .9, box: [.2, .2, .3, .5] }, { label: campaign.object_b, confidence: .8, box: [.6, .4, .75, .7] }] }], result: { accepted: true, code: 'accepted', message: `Machine-only verdict ${slot}`, checks: [{ name: 'reference_position', passed: true, detail: 'Reference stays within recorded tolerance.' }], evidence: {}, verifier_version: 'fixture-v1' }, error: null });
  const pair: any = { id: 'pair-0001', session_id: 'session-0001', created_at: '2026-09-26T12:00:00Z', export_status: 'disabled', review: { status: 'pending', version: 0 }, manifest: { schema_version: 'counterpart.pair.v1', pair_id: 'pair-0001', session_id: 'session-0001', recipe: 'spatial', mode: campaign.mode, objects: { a: campaign.object_a, b: campaign.object_b }, baseline: capture('baseline'), counterpart: capture('counterpart'), split: 'train', provenance: { evidence_kind: options.replay ? 'fixture_replay' : 'roboflow_capture', human_reviewed: false } } };
  const campaigns = options.empty ? [] : [campaign];
  const invites: any[] = [];
  const releases: any[] = [];
  const session: any = { id: 'session-joined', recipe: campaign.recipe, mode: campaign.mode, object_a: campaign.object_a, object_b: campaign.object_b, campaign_id: campaign.id, revision: 0, epoch: 0, state: 'collecting', recovery_required: false, baseline: null, counterpart: null, latest_capture: null, feedback: null, pair: null, events: [] };
  let firstReview = true;
  let firstJoin = true;
  const joined = new Map<string, { token: string; session: any }>();
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname; const method = request.method(); requestedUrls.push(request.url());
    const body = method === 'POST' ? request.postDataJSON() : null;
    if (method === 'POST') posts.push({ path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/health') return json({ live_enabled: true, provider: 'roboflow' });
    if (path === '/api/recipes') return json({ recipes: [{ recipe: 'spatial', supported_objects: ['bottle', 'cup', 'book', 'cell phone', 'mouse'] }] });
    if (path === '/api/operator/session') return json({ authenticated, local_mode: options.local ?? false });
    if (path === '/api/operator/login' || path === '/api/operator/bootstrap') { authenticated = true; return json({ authenticated: true }); }
    if (path === '/api/operator/logout') { authenticated = false; return json({ authenticated: false }); }
    if (path === '/api/operator/campaigns') { if (method === 'POST') { Object.assign(campaign, body); campaigns.push(campaign); return json(campaign); } return json({ campaigns }); }
    if (path === '/api/operator/destination') return json({ enabled: false, reason: 'Private dataset destination is not configured.' });
    if (path === `/api/operator/campaigns/${campaign.id}`) return json(campaign);
    if (path === `/api/operator/campaigns/${campaign.id}/pairs`) return json({ pairs: [pair] });
    if (path === `/api/operator/campaigns/${campaign.id}/sessions`) return json({ sessions: [{ id: pair.session_id, recipe: campaign.recipe, mode: campaign.mode, state: 'published', created_at: campaign.created_at, pair_id: pair.id, review_status: pair.review.status, capture_count: 3, failed_capture_count: 1 }] });
    if (path === `/api/operator/campaigns/${campaign.id}/activity`) { campaign.active = body.active; return json(campaign); }
    if (path.endsWith('/review')) {
      if (options.reviewConflict && firstReview) { firstReview = false; pair.review = { status: 'rejected', version: 1, reason: 'Reference was not stationary.', reviewer: 'workspace-operator' }; return json({ detail: { code: 'stale_review', message: 'Review changed' } }, 409); }
      if (body.expected_version !== pair.review.version) return json({ detail: { code: 'stale_review', message: 'Review changed' } }, 409);
      pair.review = { status: body.decision, version: pair.review.version + 1, reason: body.reason, reviewer: 'workspace-operator' };
      progress.approved_pairs = body.decision === 'approved' ? 1 : 0; progress.pending_review_pairs = 0; return json(pair.review);
    }
    if (path.endsWith('/invites')) { if (method === 'POST') { const invite = { ...body, id: 'invite-0001', campaign_id: campaign.id, expires_at: '2030-09-27T12:00:00Z', revoked_at: null, sessions_created: 0, token: 'collector-fragment-secret' }; invites.push({ ...invite, token: undefined }); return json(invite); } return json({ invites }); }
    if (path.endsWith('/revoke')) { invites[0].revoked_at = '2026-09-26T13:00:00Z'; return json(invites[0]); }
    if (path.endsWith('/releases') && path.includes('/campaigns/')) { if (method === 'POST') { const release = { id: 'release-0001', campaign_id: campaign.id, pair_count: body.pair_ids.length, created_at: campaign.created_at, split_counts: { train: body.pair_ids.length, valid: 0, test: 0 } }; releases.push(release); return json(release); } return json({ releases }); }
    if (path === '/api/operator/releases/release-0001/archive') return route.fulfill({ contentType: 'application/zip', body: 'test archive' });
    if (path === '/api/operator/releases/release-0001/manifest') return json(releases[0]);
    if (path === '/api/operator/pairs/pair-0001/export') return json({ status: 'uncertain', can_recheck: true, recheck_reason: 'Search visibility can be rechecked without another upload.', receipts: [{ asset_id: 'asset-baseline', state: 'upload_uncertain' }] });
    if (path === '/api/operator/pairs/pair-0001/export/recheck') return json({ status: 'pending' });
    if (path === '/api/operator/sessions/session-0001') return json({ ...session, id: pair.session_id, state: 'published', captures: [pair.manifest.baseline, { ...pair.manifest.counterpart, id: 'rejected-attempt', status: 'rejected', result: { ...pair.manifest.counterpart.result, accepted: false, message: 'Reference moved in this rejected attempt.' } }, pair.manifest.counterpart] });
    if (path.includes('/assets/')) return route.fulfill({ contentType: 'image/svg+xml', body: drawing });
    if (path === '/api/invites/info') return json({ campaign, accepting: true, invite: { id: 'invite-0001', label: 'Collector', expires_at: '2030-09-27T12:00:00Z', remaining_sessions: 10 } });
    if (path === '/api/invites/sessions') {
      if (!joined.has(body.idempotency_key)) { const next = joined.size ? { ...session, id: `session-joined-${joined.size + 1}` } : session; joined.set(body.idempotency_key, { token: `joined-session-capability-${joined.size + 1}`, session: next }); }
      if (options.failFirstJoin && firstJoin) { firstJoin = false; return route.abort('failed'); }
      return json(joined.get(body.idempotency_key));
    }
    if (path.startsWith('/api/sessions/session-joined')) { const id = path.split('/')[3]; const found = [...joined.values()].find(entry => entry.session.id === id); if (found) { if (path.endsWith('/resume')) found.session.epoch++; return json(found.session); } }
    if (path === '/api/sessions/session-joined/resume') { session.epoch++; return json(session); }
    if (path === '/api/sessions/session-joined') return json(session);
    return json({ detail: { message: `Unexpected fixture request ${method} ${path}` } }, 500);
  });
  return { campaign, pair, posts, requestedUrls, session };
}

async function openCollection(page: Page) {
  await page.goto('/#/workspace');
  await page.getByRole('button', { name: /Objects, opposite stories/ }).click();
  await expect(page.getByRole('heading', { name: 'Objects, opposite stories.' })).toBeVisible();
}

test('operator login keeps token out of browser storage and creates supported-role collection', async ({ page }) => {
  const state = await mockProduct(page, { authenticated: false, empty: true });
  await page.goto('/#/workspace');
  await page.getByLabel('Operator access token').fill('operator-test-secret');
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await page.getByRole('button', { name: 'New collection' }).click();
  await page.getByLabel('Collection name').fill('Books, two sides');
  await page.getByLabel('Changing object').selectOption('book');
  await page.getByLabel('Reference object').selectOption('mouse');
  await page.getByRole('button', { name: 'Create collection', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Books, two sides.' })).toBeVisible();
  expect(state.posts.find(post => post.path === '/api/operator/campaigns')?.body).toMatchObject({ object_a: 'book', object_b: 'mouse', mode: 'live' });
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(storage).not.toContain('operator-test-secret');
  expect(await page.locator('body').innerText()).not.toContain('operator-test-secret');
});

test('review actual saved evidence, hide assessment, approve and freeze downloadable release', async ({ page }) => {
  const state = await mockProduct(page);
  await openCollection(page);
  await expect(page.getByLabel('Select pair pair-000')).toBeDisabled();
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await expect(page.getByText('Machine-only verdict baseline')).toBeVisible();
  await page.getByLabel('Hide automated assessment and expected answers').check();
  await expect(page.getByText('Machine-only verdict baseline')).toHaveCount(0);
  await expect(page.getByText('Book left', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await page.getByLabel('Review note').fill('The intended difference is visible; the reference is consistent.');
  await page.getByRole('button', { name: 'Approve pair' }).click();
  expect(state.posts.find(post => post.path.endsWith('/review'))?.body).toEqual(expect.objectContaining({ expected_version: 0, decision: 'approved' }));
  expect(state.posts.find(post => post.path.endsWith('/review'))?.body).not.toHaveProperty('reviewer');
  await page.getByRole('button', { name: 'Select approved', exact: true }).click();
  await page.getByRole('button', { name: 'Create dataset release' }).click();
  await expect(page.getByText('1 approved pair', { exact: true })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download dataset ZIP' }).click();
  expect((await download).suggestedFilename()).toBe('counterpart-release-release-.zip');
  await expect(page.getByText('Private dataset destination is not configured.')).toBeVisible();
});

test('concurrent review cannot silently adopt a newer optimistic version', async ({ page }) => {
  const state = await mockProduct(page, { reviewConflict: true });
  await openCollection(page);
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await page.getByRole('button', { name: 'Approve pair' }).click();
  await expect(page.getByText('Reference was not stationary.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve pair' })).toBeDisabled();
  await page.getByRole('button', { name: 'I have read the current review' }).click();
  await page.getByLabel('Review note').fill('Rechecked the original frames.');
  await page.getByRole('button', { name: 'Approve pair' }).click();
  expect(state.posts.filter(post => post.path.endsWith('/review')).map(post => post.body.expected_version)).toEqual([0, 1]);
});

test('phone invitation uses fragment capability, supported custom roles, QR and persistent session library', async ({ page }) => {
  const state = await mockProduct(page);
  await openCollection(page);
  await page.getByRole('tab', { name: 'Invite collectors' }).click();
  await page.getByRole('button', { name: 'Create collector invitation' }).click();
  const link = await page.getByLabel('Collector invitation link').inputValue();
  expect(new URL(link).search).toBe(''); expect(new URL(link).hash).toBe('#/join/collector-fragment-secret');
  await expect(page.getByRole('img', { name: 'Scan to open the collector invitation' })).toBeVisible();
  await page.getByRole('link', { name: 'Open collector view' }).click();
  await expect(page.getByText('One book, one mouse, and a steady camera.')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Start collecting' }).click();
  await expect(page.getByRole('heading', { name: 'Place the book to the left of the mouse.' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Setup guide for book and mouse' })).toBeVisible();
  expect(state.requestedUrls.every(url => !url.includes('collector-fragment-secret'))).toBe(true);
  expect(state.posts.find(post => post.path === '/api/invites/sessions')?.body).toMatchObject({ token: 'collector-fragment-secret' });
  await page.getByRole('link', { name: 'Your sessions' }).click();
  await expect(page.getByRole('heading', { name: 'Switch sides' })).toBeVisible();
  await page.getByRole('button', { name: 'Open session' }).click();
  await expect(page.getByRole('heading', { name: 'Place the book to the left of the mouse.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('replay release requires explicit test labeling and custom class choices reset', async ({ page }) => {
  await mockProduct(page, { replay: true });
  await page.goto('/#/workspace');
  await page.getByRole('button', { name: 'New collection' }).click();
  await page.getByLabel('Changing object').selectOption('book');
  await page.getByLabel('Collection source').selectOption('replay');
  await expect(page.getByLabel('Changing object')).toHaveValue('bottle');
  await expect(page.getByLabel('Changing object')).toBeDisabled();
  await page.getByRole('button', { name: 'Close collection form' }).click();
  await page.getByRole('button', { name: /Objects, opposite stories/ }).click();
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await page.getByRole('button', { name: 'Approve pair' }).click();
  await page.getByRole('button', { name: 'Select approved', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create dataset release' })).toBeDisabled();
  await page.getByLabel('Create a labeled test release, excluded from real data.').check();
  await expect(page.getByRole('button', { name: 'Create dataset release' })).toBeEnabled();
});

test('operator can inspect rejected evidence and recheck an uncertain upload without uploading again', async ({ page }) => {
  const state = await mockProduct(page);
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await expect(page.getByText('Reference moved in this rejected attempt.', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Second saved capture', exact: true })).toHaveCount(2);
  await page.getByRole('tab', { name: 'Review pairs (1)' }).click();
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await page.getByText('Roboflow export receipts', { exact: true }).click();
  await page.getByRole('button', { name: 'Recheck existing upload receipts' }).click();
  expect(state.posts.filter(post => post.path.includes('/export/')).map(post => post.path)).toEqual(['/api/operator/pairs/pair-0001/export/recheck']);
});

test('mobile workspace and review remain readable without horizontal overflow', async ({ page }) => {
  await mockProduct(page, { local: true, authenticated: false });
  await page.setViewportSize({ width: 390, height: 844 });
  await openCollection(page);
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await expect(page.getByRole('button', { name: 'Approve pair' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});


test('lost join response reuses request identity and later invitation reuse preserves every session and pending upload', async ({ page }) => {
  const state = await mockProduct(page, { failFirstJoin: true });
  const invitation = '/#/join/collector-fragment-secret';
  await page.goto(invitation);
  await page.getByRole('button', { name: 'Start collecting' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Start collecting' }).click();
  await expect(page.getByRole('heading', { name: 'Place the book to the left of the mouse.' })).toBeVisible();
  await page.evaluate(async () => {
    await new Promise<void>((resolve, reject) => {
      const opening = indexedDB.open('counterpart-capture', 2);
      opening.onerror = () => reject(opening.error);
      opening.onsuccess = () => {
        const db = opening.result, tx = db.transaction('pending', 'readwrite');
        tx.objectStore('pending').put({ sessionId: 'session-joined', createdAt: Date.now(), blobs: [new Blob(['retained image bytes'], { type: 'image/jpeg' })], metadata: { slot: 'baseline', expected_revision: 0, epoch: 0, client_seq: 1, idempotency_key: 'pending-first-session', timestamps_ms: [0], interrupted: false } });
        tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = () => reject(tx.error);
      };
    });
  });
  await page.goto(invitation);
  await page.getByRole('button', { name: 'Start collecting' }).click();
  await expect(page.getByRole('heading', { name: 'Place the book to the left of the mouse.' })).toBeVisible();
  const joins = state.posts.filter(post => post.path === '/api/invites/sessions');
  expect(joins).toHaveLength(3);
  expect(joins[0].body.idempotency_key).toBe(joins[1].body.idempotency_key);
  expect(joins[2].body.idempotency_key).not.toBe(joins[0].body.idempotency_key);
  await page.getByRole('link', { name: 'Your sessions' }).click();
  await expect(page.getByRole('button', { name: 'Open session' })).toHaveCount(2);
  await page.getByRole('button', { name: 'Open session' }).nth(1).click();
  await expect(page.getByText('A capture is waiting for confirmation.')).toBeVisible();
  await page.getByRole('link', { name: 'Your sessions' }).click();
  await page.getByRole('button', { name: 'Forget from this device' }).nth(1).click();
  await page.getByRole('button', { name: 'Forget access', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('unconfirmed capture');
  await expect(page.getByRole('button', { name: 'Open session' })).toHaveCount(2);
});
