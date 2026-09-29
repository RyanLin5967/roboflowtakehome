import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// Browser/protocol fixtures only: these do not establish CV accuracy.
const oldId = 'saved-appearance-session';
const newId = 'rechecked-appearance-session';
const oldToken = 'fixture-original-capability';
const newToken = 'fixture-rechecked-capability';
const drawing = '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440"><rect width="720" height="440" fill="#ebe7df"/><rect x="100" y="130" width="80" height="190" fill="#b87d56"/><rect x="350" y="220" width="100" height="100" fill="#738a67"/></svg>';

async function savedPair(page: Page, options: { sourceVersion?: '2.0.0' | '2.1.0' | '2.2.0'; loseFirstResponse?: boolean; campaign?: boolean; human?: boolean; cupFailed?: boolean; registrationUnknown?: boolean; supported?: boolean; orientation?: 'mirrored' | 'original' | 'legacy'; allowCamera?: boolean; blockOrientationWrite?: boolean } = {}) {
  const appearance = { name: 'appearance_continuity:bottle:0', passed: false, outcome: 'unknown', code: 'appearance_uncertain', detail: "The bottle's appearance comparison is inconclusive.", measurements: { histogram_distance: 0.72 } };
  const baseChecks = [
    { name: 'bottle_right_of_cup', passed: true, outcome: 'pass', code: 'relation_visible', detail: 'The bottle is right of the cup.' },
    { name: 'reference_position', passed: !options.cupFailed, outcome: options.cupFailed ? 'fail' : 'pass', code: options.cupFailed ? 'reference_moved' : 'reference_stable', detail: options.cupFailed ? 'The cup moved.' : 'The cup stayed in place.' },
    { name: 'background_registration:0', passed: !options.registrationUnknown, outcome: options.registrationUnknown ? 'unknown' : 'pass', code: options.registrationUnknown ? 'camera_registration_unknown' : 'camera_stable', detail: options.registrationUnknown ? 'The background cannot establish camera stability.' : 'The background remained stable.' },
    appearance,
  ];
  const capture = (slot: 'baseline' | 'counterpart') => ({
    id: `original-${slot}`, slot, status: slot === 'baseline' ? 'accepted' : 'rejected', revision: slot === 'baseline' ? 1 : 2, epoch: 0,
    created_at: '2026-09-29T12:00:00Z', frames: [{ asset_id: `original-${slot}-pixels`, t_ms: 0, width: 720, height: 440 }],
    result: { accepted: slot === 'baseline', outcome: slot === 'baseline' ? 'pass' : options.cupFailed ? 'fail' : 'unknown', hard_error: false, code: slot === 'baseline' ? 'accepted' : options.cupFailed ? 'reference_moved' : options.registrationUnknown ? 'camera_registration_unknown' : 'appearance_uncertain', message: slot === 'baseline' ? 'The first scene passes.' : 'Appearance comparison is inconclusive.', checks: slot === 'baseline' ? [baseChecks[0]] : baseChecks, evidence: {}, verifier_version: `counterpart-verifier/${options.sourceVersion || '2.0.0'}` }, error: null,
  });
  const baseline = capture('baseline');
  const candidate = capture('counterpart');
  const original: any = { id: oldId, recipe: 'spatial', mode: 'live', object_a: 'bottle', object_b: 'cup', revision: 2, epoch: 0, state: 'baseline_ready', recovery_required: false, baseline, counterpart: null, latest_capture: candidate, feedback: null, pair: null, events: [], campaign_id: options.campaign ? 'frozen-campaign' : null, human_review_required: !!options.human, baseline_authorization: options.human ? { id: 'human-authorization' } : null, recipe_snapshot: { recipe_version: options.sourceVersion || '2.0.0', protected_objects: [] } };
  const acceptedAppearance = { name: appearance.name, passed: true, outcome: 'pass', code: 'appearance_consistent', detail: 'The registered object appearance is consistent.' };
  const acceptedCandidate = { ...candidate, id: 'rechecked-counterpart', status: 'accepted', result: { ...candidate.result, accepted: true, outcome: 'pass', code: 'accepted', message: 'The requested relationship and scene controls pass.', checks: [...baseChecks.slice(0, 3), acceptedAppearance], verifier_version: 'counterpart-verifier/2.2.0' } };
  const rechecked: any = { ...structuredClone(original), id: newId, revision: 2, epoch: 0, state: 'ready', recovery_required: false, baseline: { ...baseline, id: 'rechecked-baseline' }, counterpart: acceptedCandidate, latest_capture: acceptedCandidate, recipe_snapshot: { recipe_version: '2.2.0', protected_objects: [] } };
  const rechecks: Array<Record<string, unknown>> = [];
  const writes: string[] = [];
  const assets: string[] = [];
  const reads: string[] = [];
  await page.addInitScript(({ id, token, targetId, orientation, allowCamera, blockOrientationWrite }) => {
    if (!localStorage.getItem('fixture-session-initialized')) {
      localStorage.setItem('counterpart.session.v1', JSON.stringify({ id, token }));
      if (orientation && orientation !== 'legacy') localStorage.setItem(`counterpart.camera-orientation.v1:${id}`, orientation);
      localStorage.setItem('fixture-session-initialized', 'true');
    }
    if (blockOrientationWrite) {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key === `counterpart.camera-orientation.v1:${targetId}` && !(window as any).__allowOrientationStorage) throw new DOMException('Fixture storage write blocked', 'QuotaExceededError');
        return set.call(this, key, value);
      };
    }
    (window as any).__cameraRequests = 0;
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      (window as any).__cameraRequests++;
      if (allowCamera) return getUserMedia(constraints);
      throw new Error('The saved-pair flow must not request a camera.');
    };
  }, { id: oldId, token: oldToken, targetId: newId, orientation: options.orientation, allowCamera: options.allowCamera, blockOrientationWrite: options.blockOrientationWrite });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (method === 'POST') writes.push(path); else reads.push(path);
    if (path === '/api/health') return json({ status: 'ok', provider: 'roboflow', live_enabled: true, saved_pair_recheck: options.supported !== false });
    if (path === `/api/sessions/${oldId}/recheck`) {
      rechecks.push(route.request().postDataJSON());
      expect(route.request().headers().authorization).toBe(`Bearer ${oldToken}`);
      if (options.loseFirstResponse && rechecks.length === 1) return route.abort('failed');
      return json({ token: newToken, session: rechecked });
    }
    const session = path.includes(newId) ? rechecked : original;
    if (path.endsWith('/resume')) {
      const body = route.request().postDataJSON();
      if (body.expected_epoch === session.epoch) session.epoch++;
      session.recovery_required = session.state === 'baseline_ready';
      return json(session);
    }
    if (path.includes('/assets/')) {
      assets.push(path);
      return route.fulfill({ contentType: 'image/svg+xml', body: drawing });
    }
    if (path === `/api/sessions/${newId}/reset`) {
      const body = route.request().postDataJSON();
      expect(body.slot).toBe('counterpart');
      rechecked.counterpart = null; rechecked.latest_capture = rechecked.baseline; rechecked.feedback = null;
      rechecked.state = 'baseline_ready'; rechecked.recovery_required = false; rechecked.revision++; rechecked.epoch++;
      return json(rechecked);
    }
    if (path === `/api/sessions/${newId}/publish`) {
      expect(route.request().headers().authorization).toBe(`Bearer ${newToken}`);
      rechecked.state = 'published'; rechecked.pair = { id: 'rechecked-pair', manifest: { baseline: rechecked.baseline, counterpart: rechecked.counterpart } };
      return json({ pair: rechecked.pair, session: rechecked });
    }
    if (path === `/api/sessions/${oldId}` || path === `/api/sessions/${newId}`) return json(session);
    return json({ detail: { code: 'fixture_unexpected_request', message: `Unexpected request: ${method} ${path}` } }, 500);
  });
  return { original, rechecked, rechecks, writes, assets, reads };
}

test('saved appearance-only uncertainty automatically rechecks the original photos and can publish without a camera or review', async ({ page }) => {
  const service = await savedPair(page);
  const originalFrames = structuredClone([service.original.baseline.frames, service.original.latest_capture.frames]);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toHaveLength(1);
  expect(service.rechecks[0]).toMatchObject({ expected_revision: 2, epoch: 1 });
  expect(service.writes.filter(path => path.endsWith('/captures'))).toHaveLength(0);
  expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
  await expect(page.getByRole('button', { name: 'Review these two photos' })).toHaveCount(0);
  await expect(page.getByText(/Appearance comparison is uncertain/i)).toHaveCount(0);
  await page.getByText('View all 4 recorded checks', { exact: true }).click();
  await expect(page.getByText('The registered object appearance is consistent.', { exact: true })).toBeVisible();
  expect(service.rechecked.counterpart.result.checks.every((check: { outcome: string; passed: boolean }) => check.outcome === 'pass' && check.passed)).toBe(true);
  expect(service.rechecked.counterpart.result).not.toHaveProperty('warnings');
  const workspace = page.getByRole('region', { name: 'Capture workspace' });
  await expect(workspace.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await expect(workspace.getByRole('img', { name: 'Second saved capture', exact: true })).toBeVisible();
  expect([service.rechecked.baseline.frames, service.rechecked.counterpart.frames]).toEqual(originalFrames);
  expect(service.assets).toContain(`/api/sessions/${newId}/assets/original-baseline-pixels`);
  expect(service.assets).toContain(`/api/sessions/${newId}/assets/original-counterpart-pixels`);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('counterpart.session.v1')!))).toEqual({ id: newId, token: newToken });
  await page.getByRole('button', { name: 'Save the complete pair', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Download complete pair', exact: true })).toBeEnabled();
  expect(service.original.latest_capture.result.accepted).toBe(false);
  expect(service.original.latest_capture.result.checks.at(-1)).toMatchObject({ outcome: 'unknown', passed: false, code: 'appearance_uncertain' });
  expect(service.original.recipe_snapshot.recipe_version).toBe('2.0.0');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Download complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toHaveLength(1);
});

test('a lost recheck acknowledgement retries the identical saved request without repeated automatic work', async ({ page }) => {
  const service = await savedPair(page, { loseFirstResponse: true });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Retry saved-photo check', exact: true })).toBeEnabled();
  expect(service.rechecks).toHaveLength(1);
  const originalRequest = structuredClone(service.rechecks[0]);
  await expect.poll(() => service.reads.filter(path => path === `/api/sessions/${oldId}`).length).toBeGreaterThan(1);
  expect(service.rechecks).toHaveLength(1);
  await page.getByRole('button', { name: 'Retry saved-photo check', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toEqual([originalRequest, originalRequest]);
  expect(service.writes.filter(path => path.endsWith('/captures'))).toHaveLength(0);
  expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
});

test('reloading after a lost acknowledgement retains the exact recheck identity across a new connection epoch', async ({ page }) => {
  const service = await savedPair(page, { loseFirstResponse: true });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Retry saved-photo check', exact: true })).toBeEnabled();
  const originalRequest = structuredClone(service.rechecks[0]);
  await page.reload();
  await expect.poll(async () => service.rechecks.length > 1 || await page.getByRole('button', { name: 'Retry saved-photo check', exact: true }).isVisible()).toBe(true);
  const retry = page.getByRole('button', { name: 'Retry saved-photo check', exact: true });
  if (await retry.isVisible()) await retry.click();
  await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toEqual([originalRequest, originalRequest]);
  expect(service.writes.filter(path => path.endsWith('/captures'))).toHaveLength(0);
  expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
});

for (const orientation of ['mirrored', 'original', 'legacy'] as const) {
  test(`rechecked ${orientation} camera coordinates survive reload and a second-photo retake`, async ({ page }) => {
    const service = await savedPair(page, { orientation, allowCamera: true });
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
    const expected = orientation === 'mirrored' ? 'mirrored' : 'original';
    expect(await page.evaluate(id => localStorage.getItem(`counterpart.camera-orientation.v1:${id}`), newId)).toBe(expected);
    await page.reload();
    await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Retake second image', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Capture the second image', exact: true })).toBeEnabled();
    await page.getByText('Camera options', { exact: true }).click();
    const mirror = page.getByRole('checkbox', { name: 'Mirror camera', exact: true });
    await expect(mirror).toBeDisabled();
    if (orientation === 'mirrored') await expect(mirror).toBeChecked(); else await expect(mirror).not.toBeChecked();
    await expect(page.getByLabel('Live camera view')).toHaveCSS('transform', orientation === 'mirrored' ? 'matrix(-1, 0, 0, 1, 0, 0)' : 'none');
    expect(service.rechecks).toHaveLength(1);
    expect(service.writes.filter(path => path.endsWith('/captures'))).toHaveLength(0);
  });
}

test('blocked orientation storage retains the original connection and safely retries its recheck receipt', async ({ page }) => {
  const service = await savedPair(page, { orientation: 'mirrored', blockOrientationWrite: true });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Retry saved-photo check', exact: true })).toBeEnabled();
  await expect(page.getByRole('alert')).toContainText('could not preserve their camera orientation');
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('counterpart.session.v1')!))).toEqual({ id: oldId, token: oldToken });
  expect(await page.evaluate(id => localStorage.getItem(`counterpart.camera-orientation.v1:${id}`), newId)).toBeNull();
  expect(service.rechecks).toHaveLength(1);
  await page.evaluate(() => { (window as any).__allowOrientationStorage = true; });
  await page.getByRole('button', { name: 'Retry saved-photo check', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toHaveLength(2);
  expect(service.rechecks[1]).toEqual(service.rechecks[0]);
  expect(await page.evaluate(id => localStorage.getItem(`counterpart.camera-orientation.v1:${id}`), newId)).toBe('mirrored');
  expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
});

for (const [label, options] of [
  ['current verification already applied', { sourceVersion: '2.2.0' }],
  ['known cup movement', { cupFailed: true }],
  ['uncertain camera registration', { registrationUnknown: true }],
  ['a frozen campaign contract', { campaign: true }],
  ['human baseline authorization', { human: true }],
  ['a server without saved-pair recheck support', { supported: false }],
] as const) {
  test(`does not automatically replace ${label}`, async ({ page }) => {
    const service = await savedPair(page, options);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Two photos, one change' })).toBeVisible();
    // Observe a post-hydration poll, rather than only inspecting initial render.
    await expect.poll(() => service.reads.filter(path => path === `/api/sessions/${oldId}`).length).toBeGreaterThan(1);
    expect(service.rechecks).toHaveLength(0);
    await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toHaveCount(0);
    expect(service.original.recipe_snapshot.recipe_version).toBe('sourceVersion' in options ? options.sourceVersion : '2.0.0');
    expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
  });
}


test('saved 2.1 photos recheck to the new verifier without retaking or losing their original decision', async ({ page }) => {
  const service = await savedPair(page, { sourceVersion: '2.1.0' });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled();
  expect(service.rechecks).toHaveLength(1);
  expect(service.original.recipe_snapshot.recipe_version).toBe('2.1.0');
  expect(service.original.latest_capture.result.accepted).toBe(false);
  expect(service.rechecked.recipe_snapshot.recipe_version).toBe('2.2.0');
  expect(await page.evaluate(() => (window as any).__cameraRequests)).toBe(0);
});
