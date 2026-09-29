import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// Protocol and UI fixtures. These are deliberately not model-quality evidence.
const drawing = '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440"><rect width="720" height="440" fill="#e7e2d5"/><rect x="140" y="140" width="100" height="175" fill="#b57b5a"/><rect x="430" y="210" width="110" height="100" fill="#809275"/></svg>';
async function mockProduct(page: Page, options: { authenticated?: boolean; local?: boolean; empty?: boolean; replay?: boolean; reviewConflict?: boolean; failFirstJoin?: boolean; destination?: boolean; authorizable?: boolean } = {}) {
  let authenticated = options.authenticated ?? true;
  const posts: Array<{ path: string; body: any }> = [];
  const requestedUrls: string[] = [];
  const progress = { sessions: 1, published_pairs: 1, approved_pairs: 0, rejected_pairs: 0, needs_revision_pairs: 0, pending_review_pairs: 1, target_pairs: 12, remaining_pairs: 12 };
  const campaign: any = { id: 'campaign-0001', name: 'Objects, opposite stories', active: true, recipe: 'spatial', mode: options.replay ? 'replay' : 'live', object_a: options.replay ? 'bottle' : 'book', object_b: options.replay ? 'cup' : 'mouse', recipe_snapshot: {}, target_pairs: 12, created_at: '2026-09-26T12:00:00Z', progress };
  const capture = (slot: string): any => ({ id: `capture-${slot}`, slot, status: 'accepted', revision: slot === 'baseline' ? 1 : 2, epoch: 0, created_at: '2026-09-26T12:00:00Z', frames: [{ asset_id: `asset-${slot}`, width: 720, height: 440, t_ms: 0, detections: [{ label: campaign.object_a, confidence: .9, box: [.2, .2, .3, .5] }, { label: campaign.object_b, confidence: .8, box: [.6, .4, .75, .7] }] }], result: { accepted: true, code: 'accepted', message: `Machine-only verdict ${slot}`, checks: [{ name: 'reference_position', passed: true, detail: 'Reference stays within recorded tolerance.' }], evidence: {}, verifier_version: 'fixture-v1' }, error: null });
  const pair: any = { id: 'pair-0001', session_id: 'session-0001', created_at: '2026-09-26T12:00:00Z', export_status: 'disabled', review: { status: 'pending', version: 0 }, manifest: { schema_version: 'counterpart.pair.v1', pair_id: 'pair-0001', session_id: 'session-0001', recipe: 'spatial', mode: campaign.mode, objects: { a: campaign.object_a, b: campaign.object_b }, baseline: capture('baseline'), counterpart: capture('counterpart'), split: 'train', provenance: { evidence_kind: options.replay ? 'fixture_replay' : 'roboflow_capture', human_reviewed: false } } };
  const campaigns = options.empty ? [] : [campaign];
  const invites: any[] = [];
  const splitGroups: any[] = [];
  const evidenceRevisions: any[] = [];
  const adjudications: any[] = [];
  const attemptReviews: any[] = [];
  let baselineAuthorization: any = null;
  const annotationSnapshots: any[] = [];
  const deliveries: any[] = [];
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
    if (path === '/api/operator/split-groups') { if (method === 'POST') { const group = { id: `group-${splitGroups.length + 1}`, name: body.name, description: body.description || '', created_at: campaign.created_at }; splitGroups.push(group); return json(group); } return json({ split_groups: splitGroups }); }
    if (path === '/api/operator/destination') return json(options.destination ? { enabled: true, workspace: 'private-workspace', project: 'pair-release', privacy: 'private' } : { enabled: false, reason: 'Private dataset destination is not configured.' });
    if (path === `/api/operator/campaigns/${campaign.id}`) return json(campaign);
    if (path === `/api/operator/campaigns/${campaign.id}/pairs`) return json({ pairs: [pair] });
    if (path === `/api/operator/campaigns/${campaign.id}/sessions`) return json({ sessions: [{ id: pair.session_id, recipe: campaign.recipe, mode: campaign.mode, state: 'published', created_at: campaign.created_at, pair_id: pair.id, review_status: pair.review.status, capture_count: 3, failed_capture_count: 1 }] });
    if (path === `/api/operator/campaigns/${campaign.id}/activity`) { campaign.active = body.active; return json(campaign); }
    if (path.includes('/captures/') && path.endsWith('/review')) { const item = { id: 'attempt-review-1', version: attemptReviews.length + 1, decision: body.decision, reason: body.reason, created_at: campaign.created_at }; attemptReviews.push(item); return json(item); }
    if (path.endsWith('/review')) {
      if (options.reviewConflict && firstReview) { firstReview = false; pair.review = { status: 'rejected', version: 1, reason: 'Reference was not stationary.', reviewer: 'workspace-operator' }; return json({ detail: { code: 'stale_review', message: 'Review changed' } }, 409); }
      if (body.expected_version !== pair.review.version) return json({ detail: { code: 'stale_review', message: 'Review changed' } }, 409);
      pair.review = { status: body.decision, version: pair.review.version + 1, reason: body.reason, reviewer: 'workspace-operator' };
      progress.approved_pairs = body.decision === 'approved' ? 1 : 0; progress.pending_review_pairs = 0; return json(pair.review);
    }
    if (path.endsWith('/invites')) { if (method === 'POST') { const invite = { ...body, split_group_name: splitGroups.find(group => group.id === body.split_group_id)?.name, id: 'invite-0001', campaign_id: campaign.id, expires_at: '2030-09-27T12:00:00Z', revoked_at: null, sessions_created: 0, token: 'collector-fragment-secret' }; invites.push({ ...invite, token: undefined }); return json(invite); } return json({ invites }); }
    if (path.endsWith('/revoke')) { invites[0].revoked_at = '2026-09-26T13:00:00Z'; return json(invites[0]); }
    if (path.endsWith('/releases') && path.includes('/campaigns/')) { if (method === 'POST') { const release = { id: 'release-0001', campaign_id: campaign.id, pair_count: body.pair_ids.length, created_at: campaign.created_at, split_counts: { train: body.pair_ids.length, valid: 0, test: 0 } }; releases.push(release); return json(release); } return json({ releases }); }
    if (path === '/api/operator/releases/release-0001/roboflow') return json({ release_id: 'release-0001', generation_enabled: false, snapshots: annotationSnapshots.map(item => ({ id: item.id, content_sha256: item.content_sha256, created_at: item.created_at, approved_at: item.approval?.created_at })), deliveries });
    if (path === '/api/operator/releases/release-0001/roboflow/snapshots') { const snapshot = { id: 'annotation-0001', release_id: 'release-0001', content_sha256: 'a'.repeat(64), created_at: campaign.created_at, approval: null, manifest: { destination: { workspace: 'private-workspace', project: 'pair-release' }, images: [{ asset_id: 'asset-baseline', image_id: 'remote-1', pair_id: pair.id, split: 'train', width: 720, height: 440, machine_detections: pair.manifest.baseline.frames[0].detections, boxes: [{ label: 'book', bbox: [145, 140, 95, 170] }], annotation_sha256: 'b'.repeat(64) }], provider_review_attested: false } }; annotationSnapshots.push(snapshot); return json(snapshot); }
    if (path === '/api/operator/roboflow/snapshots/annotation-0001') return json(annotationSnapshots[0]);
    if (path === '/api/operator/roboflow/snapshots/annotation-0001/approve') { annotationSnapshots[0].approval = { reviewer: 'workspace-operator', reason: body.reason, created_at: campaign.created_at }; return json(annotationSnapshots[0]); }
    if (path === '/api/operator/roboflow/snapshots/annotation-0001/deliver') { const delivery = { id: 'delivery-0001', snapshot_id: 'annotation-0001', status: 'planned', provider_version: null, last_error: null }; deliveries.push(delivery); return json(delivery); }
    if (path === '/api/operator/releases/release-0001/archive') return route.fulfill({ contentType: 'application/zip', body: 'test archive' });
    if (path === '/api/operator/releases/release-0001/manifest') return json(releases[0]);
    if (path === '/api/operator/pairs/pair-0001/export') return json({ status: 'uncertain', can_recheck: true, recheck_reason: 'Search visibility can be rechecked without another upload.', receipts: [{ asset_id: 'asset-baseline', state: 'upload_uncertain' }] });
    if (path === '/api/operator/pairs/pair-0001/export/recheck') return json({ status: 'pending' });
    if (path === '/api/operator/sessions/session-0001/evidence') return json({ session_id: pair.session_id, session_revision: 3, selected_pair: { baseline_id: 'capture-baseline', counterpart_id: 'uncertain-counterpart', evidence_sha256: 'f'.repeat(64) }, recipe_versions: ['1.0.0', '2.0.0'], captures: [pair.manifest.baseline, { ...pair.manifest.counterpart, id: 'uncertain-counterpart', status: 'rejected', result: { ...pair.manifest.counterpart.result, accepted: false, outcome: 'unknown', message: 'The camera could not be registered.' } }].map(capture => ({ capture, evidence_sha256: 'e'.repeat(64), revision_version: evidenceRevisions.length, revisions: evidenceRevisions, review_version: attemptReviews.length, reviews: attemptReviews, baseline_authorization_eligibility: { allowed: options.authorizable && capture.slot === 'baseline', code: 'visual_unknown', message: 'Visual checks need human review.' }, review_eligibility: { allowed: true, code: 'reviewable', message: 'Original evidence is available for a separate human decision.' } })), adjudications, baseline_authorization: baselineAuthorization, baseline_authorization_criteria: [{ id: 'original_media_reviewed', label: 'Original baseline media reviewed' }, { id: 'objects_identified', label: 'Objects identified in the baseline' }, { id: 'initial_relation_confirmed', label: 'Initial relation confirmed' }, { id: 'uncertain_visual_controls_reviewed', label: 'Uncertain visual controls reviewed' }], adjudication_criteria: [{ id: 'original_media_reviewed', label: 'I reviewed both original captures' }, { id: 'objects_identified', label: 'I can identify the same objects' }, { id: 'relation_confirmed', label: 'The intended relation changes' }, { id: 'controls_confirmed', label: 'The reference, framing and protected objects are consistent' }] });
    if (path.endsWith('/authorize-baseline')) { baselineAuthorization = { id: 'baseline-authorization-1', created_at: campaign.created_at, reason: body.reason }; return json({ authorization: baselineAuthorization }); }
    if (path.endsWith('/reverify')) { const revision = { id: 'revision-1', version: 1, created_at: campaign.created_at, recipe_snapshot: { recipe_version: body.recipe_version }, result: { ...pair.manifest.counterpart.result, message: 'New verifier cannot establish camera alignment.', outcome: 'unknown', checks: [{ name: 'camera_alignment', passed: false, outcome: 'unknown', detail: 'Insufficient stable texture.' }] } }; evidenceRevisions.push(revision); return json(revision); }
    if (path.endsWith('/adjudications')) { adjudications.push({ id: 'adjudication-1', created_at: campaign.created_at, reason: body.reason, pair_id: 'human-pair-1' }); return json(adjudications[0]); }
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

test('standalone evidence route opens the selected pair with unreviewed labels and criteria', async ({ page }) => {
  const state = await mockProduct(page, { local: true, authenticated: false });
  await page.goto('/#/workspace/evidence/session-0001?baseline=capture-baseline&counterpart=uncertain-counterpart');
  await expect(page.getByRole('heading', { name: 'Check the pair.' })).toBeVisible();
  await expect(page.getByLabel('First observation for human review')).toHaveValue('capture-baseline');
  await expect(page.getByLabel('Counterpart observation for human review')).toHaveValue('uncertain-counterpart');
  await expect(page.getByLabel('First observation label')).toHaveValue('');
  await expect(page.getByLabel('Counterpart label')).toHaveValue('');
  await expect(page.getByLabel('I reviewed both original captures')).not.toBeChecked();
  await expect(page.getByRole('button', { name: 'Record human decision' })).toBeDisabled();
  await expect(page.getByRole('link', { name: 'Back to capture' })).toHaveAttribute('href', '#/capture');
  expect(state.posts.map(post => post.path)).toEqual(['/api/operator/bootstrap']);
  expect(state.requestedUrls.some(url => new URL(url).pathname === '/api/operator/campaigns')).toBe(false);
  const evidence = state.requestedUrls.find(url => url.includes('/session-0001/evidence?'))!;
  expect(new URL(evidence).searchParams.get('baseline_id')).toBe('capture-baseline');
  expect(new URL(evidence).searchParams.get('counterpart_id')).toBe('uncertain-counterpart');
  await page.getByLabel('First observation label').selectOption('left');
  await page.getByLabel('Counterpart label').selectOption('right');
  for (const label of ['I reviewed both original captures', 'I can identify the same objects', 'The intended relation changes', 'The reference, framing and protected objects are consistent']) await page.getByLabel(label, { exact: true }).check();
  await page.getByLabel('Human decision note').fill('I inspected both original photos and confirmed the reference remains stationary.');
  await page.getByRole('button', { name: 'Record human decision' }).click();
  await expect(page.getByRole('status')).toContainText('Human decision recorded.');
  expect(state.posts.find(post => post.path.endsWith('/adjudications'))?.body).toMatchObject({ baseline_id: 'capture-baseline', counterpart_id: 'uncertain-counterpart', labels: { baseline: 'left', counterpart: 'right' } });
});

test('standalone evidence deep link preserves operator login and never treats selection as authority', async ({ page }) => {
  const state = await mockProduct(page, { authenticated: false });
  await page.goto('/#/workspace/evidence/session-0001?baseline=capture-baseline&counterpart=uncertain-counterpart');
  await expect(page.getByLabel('Operator access token')).toBeVisible();
  expect(state.requestedUrls.some(url => url.includes('/operator/sessions/'))).toBe(false);
  await page.getByLabel('Operator access token').fill('operator-test-secret');
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Check the pair.' })).toBeVisible();
  await expect(page.getByLabel('Counterpart observation for human review')).toHaveValue('uncertain-counterpart');
  await expect(page.getByRole('button', { name: 'Record human decision' })).toBeDisabled();
  expect(state.posts.map(post => post.path)).toEqual(['/api/operator/login']);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('Operator access token')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Saved evidence decisions' })).toHaveCount(0);
});

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
  await expect(page.locator('.destination-panel').getByText('Private dataset destination is not configured.')).toBeVisible();
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
  await expect(page.getByRole('img', { name: 'Illustration: book left of mouse. The mouse stays in place.' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Illustration: book right of mouse. The mouse stays in place.' })).toBeVisible();
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

test('collection freezes protected objects and shared scene group follows invitation', async ({ page }) => {
  const state = await mockProduct(page, { empty: true });
  await page.goto('/#/workspace');
  await page.getByRole('button', { name: 'New collection' }).click();
  await page.getByLabel('Collection name').fill('One desk, repeated observations');
  await page.getByRole('checkbox', { name: 'Book', exact: true }).check();
  await page.getByRole('button', { name: 'Create collection', exact: true }).click();
  expect(state.posts.find(post => post.path === '/api/operator/campaigns')?.body.protected_objects).toEqual(['book']);
  await page.getByRole('tab', { name: 'Invite collectors' }).click();
  await page.getByLabel('Scene group', { exact: true }).selectOption('new');
  await page.getByLabel('New scene group name').fill('North desk, same objects');
  await page.getByRole('button', { name: 'Create collector invitation' }).click();
  await expect(page.getByText('Scene group: North desk, same objects', { exact: true })).toBeVisible();
  expect(state.posts.find(post => post.path.endsWith('/invites'))?.body.split_group_id).toBe('group-1');
  await page.getByRole('button', { name: 'Create collector invitation' }).click();
  expect(state.posts.filter(post => post.path === '/api/operator/split-groups')).toHaveLength(1);
});

test('saved evidence can append an unknown verifier revision without rewriting the original', async ({ page }) => {
  const state = await mockProduct(page);
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Decision history · 0 revisions', { exact: true }).first().click();
  await page.getByLabel('Verifier version for capture-baseline').selectOption('2.0.0');
  await page.getByRole('button', { name: 'Append verification revision' }).click();
  await expect(page.getByText('New verifier cannot establish camera alignment.').first()).toBeVisible();
  await expect(page.getByText('Machine-only verdict baseline').first()).toBeVisible();
  await page.getByText('Recorded checks', { exact: true }).first().click();
  await expect(page.getByText('Unknown', { exact: true }).first()).toBeVisible();
  const request = state.posts.find(post => post.path.endsWith('/reverify'));
  expect(request?.body).toMatchObject({ recipe_version: '2.0.0', expected_version: 0, expected_evidence_sha256: 'e'.repeat(64) });
  expect(state.posts.some(post => post.path.endsWith('/review'))).toBe(false);
});

test('uncertain pair human adjudication requires original evidence, labels and explicit controls', async ({ page }) => {
  const state = await mockProduct(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Make a human decision on an uncertain pair', { exact: true }).click();
  await page.getByLabel('First observation for human review').selectOption('capture-baseline');
  await page.getByLabel('Counterpart observation for human review').selectOption('uncertain-counterpart');
  const panel = page.locator('.adjudication-panel');
  await expect(panel.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await expect(panel.getByRole('img', { name: 'Second saved capture', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Record human decision', exact: true })).toBeDisabled();
  await page.getByLabel('First observation label').selectOption('left');
  await page.getByLabel('Counterpart label').selectOption('right');
  await page.getByLabel('Human decision note').fill('Both original frames show the same desk and objects; the mouse and framing remain unchanged.');
  for (const box of await panel.getByRole('checkbox').all()) await box.check();
  await page.getByRole('button', { name: 'Record human decision', exact: true }).click();
  await expect(page.getByText('Human decision recorded.', { exact: false })).toBeVisible();
  expect(state.posts.find(post => post.path.endsWith('/adjudications'))?.body).toMatchObject({ labels: { baseline: 'left', counterpart: 'right' }, expected_session_revision: 3, expected_evidence_sha256: 'f'.repeat(64) });
  expect(state.posts.some(post => post.path.endsWith('/review'))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('attempt rejection is recorded separately without creating or approving a pair', async ({ page }) => {
  const state = await mockProduct(page);
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Record an attempt review', { exact: true }).last().click();
  await page.getByLabel('Attempt decision for uncertain-counterpart').selectOption('needs_recapture');
  await page.getByLabel('Attempt review note for uncertain-counterpart').fill('The anchor is hidden. Another capture is required.');
  await page.getByRole('button', { name: 'Save attempt review' }).click();
  await expect(page.getByText('Needs recapture', { exact: true }).last()).toBeVisible();
  expect(state.posts.find(post => post.path.includes('/captures/') && post.path.endsWith('/review'))?.body).toMatchObject({ decision: 'needs_recapture', expected_version: 0 });
  expect(state.posts.some(post => post.path.endsWith('/adjudications'))).toBe(false);
});

test('returned annotations are visually reviewed before delivery, and an unconfigured generation budget stays blocked', async ({ page }) => {
  const state = await mockProduct(page, { destination: true });
  await openCollection(page);
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await page.getByRole('button', { name: 'Approve pair' }).click();
  await page.getByRole('button', { name: 'Select approved', exact: true }).click();
  await page.getByRole('button', { name: 'Create dataset release' }).click();
  await page.getByText('Roboflow annotations & dataset version', { exact: true }).click();
  await page.getByRole('button', { name: 'Read annotations from Roboflow' }).click();
  await expect(page.getByRole('heading', { name: 'Returned Roboflow annotations' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve this annotation snapshot' })).toBeDisabled();
  await page.getByLabel('I reviewed the returned annotations for every source image.').check();
  await page.getByLabel('Annotation review note').fill('Checked the source image and the corrected book box.');
  await page.getByRole('button', { name: 'Approve this annotation snapshot' }).click();
  await page.getByRole('button', { name: 'Request dataset version' }).click();
  await expect(page.getByText('Version requested locally', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate hosted dataset version' })).toBeDisabled();
  await expect(page.getByText('Hosted version generation is disabled until a bounded provider budget is configured.')).toBeVisible();
  expect(state.posts.some(post => post.path.endsWith('/advance'))).toBe(false);
  expect(state.posts.filter(post => post.path.includes('/roboflow/')).map(post => post.path)).toEqual(['/api/operator/releases/release-0001/roboflow/snapshots', '/api/operator/roboflow/snapshots/annotation-0001/approve', '/api/operator/roboflow/snapshots/annotation-0001/deliver']);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('lost re-verification response retains operation identity and original evidence hash', async ({ page }) => {
  await mockProduct(page);
  const calls: any[] = [];
  await page.route('**/api/operator/sessions/session-0001/captures/capture-baseline/reverify', async route => {
    calls.push(route.request().postDataJSON());
    if (calls.length === 1) return route.abort('failed');
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 'revision-1' }) });
  });
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Decision history · 0 revisions', { exact: true }).first().click();
  await page.getByLabel('Verifier version for capture-baseline').selectOption('2.0.0');
  await page.getByRole('button', { name: 'Append verification revision' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: 'Append verification revision' }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(calls).toHaveLength(2);
  expect(calls[0]).toEqual(calls[1]);
});

test('known failed control cannot be converted into human-adjudicated evidence', async ({ page }) => {
  await mockProduct(page);
  await page.route('**/api/operator/sessions/session-0001/evidence*', async route => {
    const capture = (id: string, slot: string): any => ({ id, slot, status: 'rejected', revision: slot === 'baseline' ? 1 : 2, epoch: 0, created_at: '', frames: [{ asset_id: `asset-${slot}`, width: 720, height: 440, t_ms: 0 }], result: { accepted: false, outcome: 'fail', message: 'The protected reference moved.', checks: [{ name: 'reference_position', outcome: 'fail', passed: false }], evidence: {}, verifier_version: 'fixture' }, error: null });
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ session_id: 'session-0001', session_revision: 3, recipe_versions: ['1.0.0'], captures: ['baseline', 'counterpart'].map(slot => ({ capture: capture(`capture-${slot}`, slot), evidence_sha256: 'e'.repeat(64), revision_version: 0, revisions: [], review_version: 0, reviews: [], review_eligibility: { allowed: false, code: 'known_control_failure', message: 'A known failed control cannot be overridden by review.' } })), selected_pair: { baseline_id: 'capture-baseline', counterpart_id: 'capture-counterpart', evidence_sha256: 'f'.repeat(64) }, adjudication_criteria: [{ id: 'controls_confirmed', label: 'Controls confirmed' }], adjudications: [] }) });
  });
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Make a human decision on an uncertain pair', { exact: true }).click();
  await page.getByLabel('First observation for human review').selectOption('capture-baseline');
  await page.getByLabel('Counterpart observation for human review').selectOption('capture-counterpart');
  await page.getByLabel('First observation label').selectOption('left');
  await page.getByLabel('Counterpart label').selectOption('right');
  await page.getByLabel('Controls confirmed', { exact: true }).check();
  await page.getByLabel('Human decision note').fill('Even a long note cannot override a known failed control.');
  await expect(page.getByRole('button', { name: 'Record human decision' })).toBeDisabled();
  await expect(page.getByText('A known failed control cannot be overridden by review.').first()).toBeVisible();
});

test('uncertain Roboflow generation can reconcile a chosen ready version without repeating generation', async ({ page }) => {
  await mockProduct(page, { destination: true });
  let delivery: any = { id: 'uncertain-delivery', snapshot_id: 'annotation-0001', status: 'uncertain', provider_version: null, last_error: 'generation_timeout', receipt: { reconciliation_candidates: [{ version: 7, ready: true, images: 1, splits: { train: 1, valid: 0, test: 0 } }] } };
  const verifications: any[] = [];
  await page.route('**/api/operator/releases/release-0001/roboflow', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ release_id: 'release-0001', generation_enabled: false, snapshots: [], deliveries: [delivery] }) }));
  await page.route('**/api/operator/roboflow/deliveries/uncertain-delivery/verify-coco', async route => { verifications.push(route.request().postDataJSON()); delivery = { ...delivery, status: 'verified', provider_version: 7, receipt: { ...delivery.receipt, artifact: { write_receipt_recovered: false, provider_authenticated_artifact: false } } }; return route.fulfill({ contentType: 'application/json', body: JSON.stringify(delivery) }); });
  await openCollection(page);
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await page.getByRole('button', { name: 'Approve pair' }).click();
  await page.getByRole('button', { name: 'Select approved', exact: true }).click();
  await page.getByRole('button', { name: 'Create dataset release' }).click();
  await page.getByText('Roboflow annotations & dataset version', { exact: true }).click();
  await page.getByLabel('Candidate hosted version').selectOption('7');
  await page.getByText('Verify version 7 against its exported annotations', { exact: true }).click();
  const document = { images: [], annotations: [], categories: [] }; // Protocol fixture; the real server independently validates membership.
  await page.getByLabel('Train COCO annotations').setInputFiles({ name: '_annotations.coco.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) });
  await page.getByRole('button', { name: 'Verify imported COCO contents' }).click();
  await expect(page.getByText('Imported COCO contents verified', { exact: true })).toBeVisible();
  await expect(page.getByText('The imported contents match. The original generation acknowledgement remains unavailable;', { exact: false })).toBeVisible();
  expect(verifications).toEqual([{ version: 7, documents: { train: document } }]);
});

test('uncertain baseline authorization requires original evidence and explicit review before collector continuation', async ({ page }) => {
  const state = await mockProduct(page, { authorizable: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Review this baseline so the collector can continue', { exact: true }).click();
  const panel = page.locator('.baseline-authorization');
  await expect(panel.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Authorize counterpart capture' })).toBeDisabled();
  await page.getByLabel('Baseline authorization answer').selectOption('left');
  await page.getByLabel('Baseline authorization note').fill('The book is clearly left of the mouse, and I inspected the original framing and visible controls.');
  for (const box of await panel.getByRole('checkbox').all()) await box.check();
  await page.getByRole('button', { name: 'Authorize counterpart capture' }).click();
  await expect(page.getByText('Counterpart capture authorized by human review', { exact: true })).toBeVisible();
  const body = state.posts.find(post => post.path.endsWith('/authorize-baseline'))?.body;
  expect(body).toMatchObject({ expected_session_revision: 3, expected_evidence_sha256: 'e'.repeat(64), answer: 'left', criteria: { original_media_reviewed: true, objects_identified: true, initial_relation_confirmed: true, uncertain_visual_controls_reviewed: true } });
  expect(state.posts.some(post => post.path.endsWith('/adjudications'))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '../artifacts/gap-evidence-mobile-fixture.png', fullPage: true });
});

test('re-verification explains an uncertain revised baseline while preserving the strict counterpart response and original decision', async ({ page }) => {
  const state = await mockProduct(page);
  const revisions: any[] = [];
  await page.route('**/api/operator/sessions/session-0001/evidence*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ session_id: 'session-0001', session_revision: 3, recipe_versions: ['1.0.0', '2.0.0'], captures: [{ capture: state.pair.manifest.counterpart, evidence_sha256: 'e'.repeat(64), revision_version: revisions.length, revisions, review_version: 0, reviews: [], review_eligibility: { allowed: true, code: 'reviewable', message: 'Original evidence retained.' } }], selected_pair: null, adjudications: [], adjudication_criteria: [] }) }));
  await page.route('**/api/operator/sessions/session-0001/captures/capture-counterpart/reverify', route => {
    const original = state.pair.manifest.counterpart.result;
    const revision = { id: 'reference-blocked-revision', version: 1, created_at: state.campaign.created_at, recipe_snapshot: { recipe_version: '2.0.0' }, result: { ...original, accepted: false, outcome: 'fail', code: 'baseline_required', message: 'Save an accepted baseline first.', checks: [{ name: 'baseline_required', passed: false, outcome: 'fail' }] }, reference_result: { ...original, accepted: false, outcome: 'unknown', message: 'The revised baseline has insufficient stable background texture.', checks: [{ name: 'camera_registration', passed: false, outcome: 'unknown', detail: 'Background features could not establish camera alignment.' }] }, comparison: { status: 'blocked_reference_unknown', outcome: 'unknown', candidate_checks: 'dependency_gate_only', reference_capture_id: 'capture-baseline', explanation: 'The new verifier cannot establish the saved baseline, so the counterpart comparison remains unknown.' } };
    revisions.push(revision);
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(revision) });
  });
  await openCollection(page);
  await page.getByRole('tab', { name: 'Sessions (1)' }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await page.getByText('Decision history · 0 revisions', { exact: true }).click();
  await page.getByLabel('Verifier version for capture-counterpart').selectOption('2.0.0');
  await page.getByRole('button', { name: 'Append verification revision' }).click();
  await expect(page.getByText('Comparison outcome: Unknown', { exact: true })).toBeVisible();
  await expect(page.getByText('The new verifier cannot establish the saved baseline, so the counterpart comparison remains unknown.', { exact: true })).toBeVisible();
  const history = page.locator('.revision-list');
  await expect(history.getByText('Machine-only verdict counterpart', { exact: true })).toBeVisible();
  await page.getByText('Revised baseline checks', { exact: true }).click();
  await expect(page.getByText('Background features could not establish camera alignment.', { exact: true })).toBeVisible();
  await page.getByText('Recorded counterpart response', { exact: true }).click();
  await expect(page.getByText('Save an accepted baseline first.', { exact: true })).toBeVisible();
  expect(state.pair.manifest.counterpart.result.accepted).toBe(true);
  expect(revisions[0].result.code).toBe('baseline_required');
});
