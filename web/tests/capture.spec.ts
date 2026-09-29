import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// These are browser/protocol fixtures, not CV model predictions or model-quality tests.
async function mockService(page: Page, options: { live?: boolean; failFirstUpload?: boolean; rejectStaleRetry?: boolean; loseResetResponse?: boolean } = {}) {
  let counter = 0;
  let uploads = 0;
  const submitted: string[] = [];
  const resets: Array<{ slot?: 'baseline' | 'counterpart'; expected_revision: number; epoch: number; idempotency_key: string }> = [];
  const session: any = { id: 'session-1234-5678', recipe: 'spatial', mode: 'replay', object_a: 'bottle', object_b: 'cup', revision: 0, epoch: 0, state: 'collecting', recovery_required: false, baseline: null, counterpart: null, latest_capture: null, feedback: null, pair: null, events: [] };
  const makeCapture = (slot: string, accepted: boolean, message: string) => ({ id: `capture-${++counter}`, slot, status: accepted ? 'accepted' : 'rejected', revision: ++session.revision, epoch: session.epoch, created_at: new Date().toISOString(), frames: Array.from({ length: session.recipe === 'temporal' && slot !== 'recovery' ? 12 : 1 }, (_, i) => ({ asset_id: `asset-${i}`, t_ms: i * 250, width: 720, height: 440 })), result: { accepted, code: accepted ? 'accepted' : 'reference_moved', message, checks: [{ name: 'cup_position', passed: accepted, detail: accepted ? 'Reference stays within tolerance.' : 'The cup moved outside its allowed position.' }], evidence: {}, verifier_version: 'fixture-v1' }, error: null });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/health') return json({ status: 'ok', provider: 'roboflow', live_enabled: !!options.live });
    if (path === '/api/sessions' && method === 'POST') {
      Object.assign(session, route.request().postDataJSON()); return json({ token: 'test-capability-not-a-provider-key', session });
    }
    if (path.endsWith('/resume')) {
      const body = route.request().postDataJSON();
      if (body.expected_epoch === session.epoch) session.epoch++;
      session.recovery_required = !!session.baseline && !session.pair;
      return json(session);
    }
    if (path.endsWith('/reset')) {
      const body = route.request().postDataJSON();
      const duplicate = resets.some(item => item.idempotency_key === body.idempotency_key);
      resets.push(body);
      if (!duplicate) {
        if (body.slot === 'counterpart') {
          if (!session.baseline) return json({ detail: { code: 'baseline_required', message: 'Save the first capture before retaking the second.' } }, 409);
          Object.assign(session, { counterpart: null, latest_capture: session.baseline, feedback: null, state: 'baseline_ready', revision: session.revision + 1, epoch: session.epoch + 1 });
        } else {
          Object.assign(session, { baseline: null, counterpart: null, latest_capture: null, feedback: null, baseline_authorization: null, human_review_required: false, state: 'collecting', recovery_required: false, revision: session.revision + 1, epoch: session.epoch + 1 });
        }
      }
      if (options.loseResetResponse && resets.length === 1) return route.abort('failed');
      return json(session);
    }
    if (path.endsWith('/replay')) {
      const body = route.request().postDataJSON();
      const accepted = ['baseline', 'counterpart', 'recovery'].includes(body.scenario);
      const slot = body.scenario === 'baseline' ? 'baseline' : body.scenario === 'recovery' ? 'recovery' : 'counterpart';
      const capture = makeCapture(slot, accepted, accepted ? 'The requested relationship is visible.' : 'The cup moved. Put it back; your first capture is saved.');
      session.latest_capture = capture;
      if (accepted && slot === 'baseline') { session.baseline = capture; session.state = 'baseline_ready'; }
      if (accepted && slot === 'counterpart') { session.counterpart = capture; session.state = 'ready'; }
      if (accepted && slot === 'recovery') session.recovery_required = false;
      return json({ capture, session });
    }
    if (path.endsWith('/captures')) {
      const raw = route.request().postData() || '';
      const match = raw.match(/"idempotency_key":"([^"]+)"/);
      submitted.push(match?.[1] || 'missing');
      if (options.failFirstUpload && uploads++ === 0) return route.abort('failed');
      if (options.rejectStaleRetry && Number(raw.match(/"epoch":(\d+)/)?.[1]) !== session.epoch) return json({ detail: { code: 'stale_epoch', message: 'Reconnect before submitting another observation' } }, 409);
      const capture = makeCapture('baseline', true, 'The bottle is left of the cup.');
      session.baseline = capture; session.latest_capture = capture; session.state = 'baseline_ready';
      return json({ capture, session });
    }
    if (path.endsWith('/publish')) { session.state = 'published'; session.pair = { id: 'pair-1' }; return json({ pair: session.pair, session }); }
    if (path.endsWith('/archive')) return route.fulfill({ contentType: 'application/zip', body: 'fixture archive' });
    if (path.endsWith('/manifest')) return json({ id: 'pair-1', source_kind: 'fixture' });
    if (path.includes('/assets/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440"><rect width="720" height="440" fill="#ede9df"/><rect x="120" y="150" width="70" height="160" rx="12" fill="#a96543"/><rect x="350" y="225" width="110" height="90" rx="18" fill="#cad0b9"/></svg>' });
    if (path === `/api/sessions/${session.id}`) return json(session);
    return json({ detail: { message: `Unexpected test request: ${path}` } }, 500);
  });
  return { session, submitted, resets };
}

test('retaking the first image clears the pair and makes the two-photo task explicit', async ({ page }) => {
  const service = await mockService(page);
  await page.goto('/');
  await expect(page.getByText('Take two photos: bottle left of the cup, then bottle right of the cup. Keep the cup and camera still between photos.')).toBeVisible();
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await expect(page.getByRole('heading', { name: 'Two photos, one change' })).toBeVisible();
  const progress = page.getByRole('region', { name: 'Your two photos' });
  await expect(progress.getByRole('article', { name: 'Photo 1 do this first' })).toContainText('Bottle LEFT of cup');
  await expect(progress.getByRole('article', { name: 'Photo 2 then do this' })).toContainText('Bottle RIGHT of cup');
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeVisible();
  await page.getByRole('button', { name: 'Retake first image' }).click();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  expect(service.resets).toHaveLength(1);
  expect(service.session.baseline).toBeNull();
  expect(service.session.recovery_required).toBe(false);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeVisible();
});

test('retaking the second image preserves the first and publishes its replacement', async ({ page }) => {
  const service = await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('article', { name: 'Photo 1 saved', exact: true })).toBeVisible();
  const originalFirst = structuredClone(service.session.baseline);
  await page.getByRole('button', { name: 'Capture the second image' }).click();
  await expect(page.getByRole('button', { name: 'Save the complete pair' })).toBeEnabled();
  const originalSecondId = service.session.counterpart.id;
  await page.getByRole('button', { name: 'Retake second image' }).click();
  await expect(page.getByRole('article', { name: 'Photo 1 saved', exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: 'Photo 2 do this now' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Save the complete pair' })).toHaveCount(0);
  expect(service.resets).toHaveLength(1);
  expect(service.resets[0].slot).toBe('counterpart');
  expect(service.session.baseline).toEqual(originalFirst);
  expect(service.session.counterpart).toBeNull();
  expect(service.session.latest_capture).toEqual(originalFirst);
  await page.getByRole('button', { name: 'Capture the second image' }).click();
  await expect(page.getByRole('article', { name: 'Photo 2 saved', exact: true })).toBeVisible();
  expect(service.session.baseline).toEqual(originalFirst);
  expect(service.session.counterpart.id).not.toBe(originalSecondId);
  await page.getByRole('button', { name: 'Save the complete pair' }).click();
  await expect(page.getByRole('button', { name: 'Download complete pair' })).toBeEnabled();
  await expect(page.getByRole('button', { name: /Retake .* image/ })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Download complete pair' })).toBeEnabled();
  expect(service.session.baseline).toEqual(originalFirst);
});

test('phone capture introduces both photo goals before one obvious camera action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const service = await mockService(page, { live: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  const progress = page.getByRole('region', { name: 'Your two photos' });
  await expect(progress.getByRole('article')).toHaveCount(2);
  await expect(progress.getByRole('heading', { name: 'Bottle LEFT of cup' })).toBeInViewport();
  await expect(progress.getByRole('heading', { name: 'Bottle RIGHT of cup' })).toBeInViewport();
  await expect(page.getByRole('heading', { name: 'Place the bottle to the left of the cup.' })).toBeInViewport();
  await expect(page.getByRole('button', { name: 'Open camera' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Open camera' })).toBeInViewport();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeHidden();
  await expect(page.getByRole('checkbox', { name: 'Live guidance' })).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  expect(service.submitted).toHaveLength(0);
});

test('a lost reset acknowledgement blocks new capture until the same reset is reconciled', async ({ page }) => {
  const service = await mockService(page, { loseResetResponse: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await page.getByRole('button', { name: 'Retake first image' }).click();
  await expect(page.getByRole('button', { name: 'Retry reset' })).toBeEnabled();
  await expect(page.locator('.primary-action button')).toBeDisabled();
  await page.getByRole('button', { name: 'Retry reset' }).click();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  expect(service.resets).toHaveLength(2);
  expect(service.resets[1]).toEqual(service.resets[0]);
  expect(service.session.epoch).toBe(1);
});

test('second-image reset can reconcile a lost acknowledgement after polling sees it cleared', async ({ page }) => {
  const service = await mockService(page, { loseResetResponse: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await page.getByRole('button', { name: 'Capture the second image' }).click();
  await expect(page.getByRole('button', { name: 'Save the complete pair' })).toBeEnabled();
  const originalFirst = structuredClone(service.session.baseline);
  await page.getByRole('button', { name: 'Retake second image' }).click();
  // The reset committed but its response was lost. Wait for a fresh snapshot:
  // the retry must remain available even though the second image is now absent.
  await expect(page.getByRole('article', { name: 'Photo 2 do this now' })).toBeVisible();
  const secondCard = page.getByRole('article', { name: 'Photo 2 do this now' });
  await expect(secondCard.getByRole('button', { name: 'Retry reset' })).toBeEnabled();
  await expect(page.locator('.primary-action button')).toBeDisabled();
  expect(service.session.counterpart).toBeNull();
  await secondCard.getByRole('button', { name: 'Retry reset' }).click();
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeEnabled();
  expect(service.resets).toHaveLength(2);
  expect(service.resets[1]).toEqual(service.resets[0]);
  expect(service.resets[1].slot).toBe('counterpart');
  expect(service.session.epoch).toBe(1);
  expect(service.session.baseline).toEqual(originalFirst);
});

test('successful reset remains usable when deletion of a stale local upload fails', async ({ page }) => {
  await mockService(page, { live: true, failFirstUpload: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toBeVisible();
  await page.evaluate(() => {
    const remove = IDBObjectStore.prototype.delete;
    IDBObjectStore.prototype.delete = function(key) {
      if (this.name === 'pending') throw new DOMException('Fixture browser storage unavailable', 'InvalidStateError');
      return remove.call(this, key);
    };
  });
  await page.getByRole('button', { name: 'Retake first image' }).click();
  await expect(page.getByText(/Browser storage could not remove the old local upload/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry reset' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toBeVisible();
});

test('replay preserves the first capture through rejection, recovery, and complete publication', async ({ page }) => {
  await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await expect(page.getByText('SIMULATION · TEST SCENES')).toBeVisible();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toBeVisible();
  await page.getByRole('button', { name: 'Move the reference' }).click();
  await expect(page.getByText('Keep the first. Fix this one.')).toBeVisible();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toBeVisible();
  await page.getByRole('button', { name: 'Evidence & session history' }).click();
  await page.getByRole('button', { name: 'Reconnect & check recovery' }).click();
  await expect(page.getByRole('button', { name: 'Check current scene' })).toBeVisible();
  await page.getByRole('button', { name: 'Check current scene' }).click();
  await page.getByRole('button', { name: 'Capture the second image' }).click();
  await page.getByRole('button', { name: 'Save the complete pair' }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download complete pair' }).click();
  expect((await download).suggestedFilename()).toMatch(/\.zip$/);
  await expect(page.getByText('Complete local record')).toBeVisible();
});

test('temporal selection shows arrival-order instructions and sampled evidence', async ({ page }) => {
  await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: /Reverse arrival order/ }).click();
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await expect(page.getByRole('heading', { name: 'Bottle first. Pause. Then the cup.' })).toBeVisible();
  await page.getByRole('button', { name: 'Record first sequence' }).click();
  await expect(page.getByRole('heading', { name: 'Cup first. Pause. Then the bottle.' })).toBeVisible();
  await expect(page.getByRole('slider', { name: 'Sequence frame' })).toBeVisible();
  await page.getByRole('button', { name: 'Record reverse sequence' }).click();
  await expect(page.getByText('Bottle → cup')).toBeVisible();
  await expect(page.getByText('Cup → bottle')).toBeVisible();
});

test('disabled live provider is explicit and cannot start a paid session', async ({ page }) => {
  await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await expect(page.getByText(/Live verification is not enabled/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start a real capture' })).toBeDisabled();
});

test('a person masking the background gets useful capture advice while the original uncertainty is preserved', async ({ page }) => {
  const { session } = await mockService(page, { live: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  const detail = 'Background matches cover too little of the image to establish camera stability.';
  session.latest_capture = {
    id: 'uncertain-baseline', slot: 'baseline', status: 'rejected', revision: 1, epoch: 0,
    created_at: new Date().toISOString(), error: null,
    frames: [{ asset_id: 'observed-frame', width: 1024, height: 576, t_ms: 0,
      detections: [{ label: 'person', confidence: .91, box: [.06, 0, 1, .99] }] }],
    result: { accepted: false, outcome: 'unknown', code: 'registration_uncertain', message: detail,
      checks: [{ name: 'background_registration:0', passed: false, outcome: 'unknown', code: 'registration_uncertain', detail,
        measurements: { candidate_sha256: 'observed-frame', candidate_support_area: .0077, candidate_support_cells: 2 } }],
      evidence: {}, verifier_version: 'fixture-v2' },
  };
  await expect(page.getByText('Keep your body out of the camera view.')).toBeVisible();
  await expect(page.getByText(/A detected person covers most of this image/)).toBeVisible();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toHaveCount(0);
  await page.getByText('View all 1 recorded checks').click();
  await expect(page.locator('.check-details').getByText(detail)).toBeVisible();
  await expect(page.locator('.check-details').getByText('Unknown', { exact: true })).toBeVisible();
  // Advice must never replace a known failure or rely on detections from a
  // different temporal frame than the uncertain registration measurement.
  session.latest_capture.frames[0].asset_id = 'different-frame';
  await expect(page.getByText('Show more fixed background detail.')).toBeVisible();
  await expect(page.getByText(/Before saving the first image, we need background details/)).toBeVisible();
  session.latest_capture.result.outcome = 'fail';
  session.latest_capture.result.code = 'reference_moved';
  session.latest_capture.result.message = 'The cup moved. Restore its saved position.';
  await expect(page.locator('.verification-message').getByText('The cup moved. Restore its saved position.')).toBeVisible();
  await expect(page.getByText('Show more fixed background detail.')).toHaveCount(0);
});

test('camera denial is actionable and makes no capture request', async ({ page }) => {
  const service = await mockService(page, { live: true });
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Denied', 'NotAllowedError')); });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.getByText(/Camera permission was denied/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open camera' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toHaveCount(0);
  expect(service.submitted).toHaveLength(0);
});

test('failed upload preserves pixels and retries the same idempotency identity after reload', async ({ page }) => {
  const service = await mockService(page, { live: true, failFirstUpload: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Retry saved capture' }).click();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toBeVisible();
  expect(service.submitted).toHaveLength(2);
  expect(service.submitted[0]).not.toBe('missing');
  expect(service.submitted[0]).toBe(service.submitted[1]);
});

test('phone layout fits the viewport and preserves the main action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockService(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Two captures. One change./ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'test-results/mobile-collector.png', fullPage: true });
});

test('native temporal sampling saves ordered frames without invoking a model', async ({ page }) => {
  const service = await mockService(page, { live: true });
  await page.goto('/');
  await page.getByRole('button', { name: /Reverse arrival order/ }).click();
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Record first sequence' }).click();
  await expect(page.getByRole('button', { name: 'Stop & check sequence' })).toBeVisible();
  await expect.poll(async () => Number((await page.locator('.recording-chip').innerText()).match(/\d+/)?.[0]), { timeout: 7000 }).toBeGreaterThanOrEqual(9);
  await page.getByRole('button', { name: 'Stop & check sequence' }).click();
  await expect(page.getByRole('article', { name: /^(Photo|Recording) 1 saved$/ })).toBeVisible();
  expect(service.submitted).toHaveLength(1);
});

test('backgrounding interrupts a temporal attempt and prevents an evidence submission', async ({ page }) => {
  const service = await mockService(page, { live: true });
  await page.goto('/');
  await page.getByRole('button', { name: /Reverse arrival order/ }).click();
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Record first sequence' }).click();
  await expect(page.getByRole('button', { name: 'Stop & check sequence' })).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.getByRole('alert')).toContainText('Capture paused while this page was hidden');
  await expect(page.getByRole('button', { name: 'Stop & check sequence' })).toHaveCount(0);
  expect(service.submitted).toHaveLength(0);
});

test('an unreceived capture from an old epoch is retained with an explicit retake path', async ({ page }) => {
  const service = await mockService(page, { live: true, failFirstUpload: true, rejectStaleRetry: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Retry saved capture' }).click();
  await expect(page.getByRole('alert')).toContainText('Discard the local copy, recheck the scene');
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toBeDisabled();
  expect(service.submitted[0]).toBe(service.submitted[1]);
  await page.getByRole('button', { name: 'Discard local copy', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toHaveCount(0);
});

test('human-adjudicated publication renders immutable pair members even when machine references are empty', async ({ page }) => {
  const service = await mockService(page);
  const member = (slot: string, id: string) => ({ id, slot, status: 'rejected', revision: slot === 'baseline' ? 1 : 2, epoch: 0, created_at: '', frames: [{ asset_id: `asset-${id}`, t_ms: 0, width: 720, height: 440 }], result: { accepted: false, outcome: 'unknown', code: 'camera_registration_unknown', message: 'Machine could not establish registration.', checks: [], evidence: {}, verifier_version: 'fixture-v2' }, error: null });
  service.session.state = 'published';
  service.session.pair = { id: 'human-pair', manifest: { baseline: member('baseline', 'human-first'), counterpart: member('counterpart', 'human-second'), evidence_adjudication: { reason: 'Both original observations were independently reviewed.' } } };
  await page.addInitScript(() => localStorage.setItem('counterpart.session.v1', JSON.stringify({ id: 'session-1234-5678', token: 'test-capability-not-a-provider-key' })));
  await page.goto('/');
  await expect(page.getByText('Established by human review', { exact: true })).toBeVisible();
  const evidence = page.getByRole('region', { name: 'Capture workspace' });
  await expect(evidence.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await expect(evidence.getByRole('img', { name: 'Second saved capture', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download complete pair' })).toBeEnabled();
  await expect(page.getByText('Observation accepted', { exact: true })).toHaveCount(0);
  expect(service.session.baseline).toBeNull();
  expect(service.session.counterpart).toBeNull();
});

test('operator-authorized uncertain baseline captures for human review after reconnect without automatic publication', async ({ page }) => {
  const service = await mockService(page);
  service.session.baseline = { id: 'uncertain-first', slot: 'baseline', status: 'rejected', revision: 1, epoch: 0, created_at: '', frames: [{ asset_id: 'uncertain-asset', t_ms: 0, width: 720, height: 440 }], result: { accepted: false, outcome: 'unknown', code: 'camera_registration_unknown', message: 'The camera could not be registered.', checks: [], evidence: {}, verifier_version: 'fixture-v2' }, error: null };
  service.session.state = 'baseline_ready'; service.session.recovery_required = true;
  service.session.human_review_required = true; service.session.baseline_authorization = { id: 'authorization-1' };
  await page.addInitScript(() => localStorage.setItem('counterpart.session.v1', JSON.stringify({ id: 'session-1234-5678', token: 'test-capability-not-a-provider-key' })));
  await page.goto('/');
  await expect(page.getByText('Collecting for human review', { exact: true })).toBeVisible();
  await expect(page.getByText('The scene has not been reverified after reconnection;', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Capture for human review' }).click();
  await expect(page.getByRole('button', { name: 'Save the complete pair' })).toHaveCount(0);
  await expect(page.getByText('Collecting for human review', { exact: true })).toBeVisible();
  expect(service.session.pair).toBeNull();
});

test('outstanding live preview accelerates only snapshot reads, then returns to idle and pauses while hidden', async ({ page }) => {
  const service = await mockService(page, { live: true });
  const snapshots: number[] = [];
  const previewPosts: number[] = [];
  let inFlight = 0, maximumInFlight = 0;
  let readyAt = Infinity;
  let preview: any = null;
  await page.route('**/api/sessions/session-1234-5678', async route => {
    snapshots.push(Date.now()); inFlight++; maximumInFlight = Math.max(maximumInFlight, inFlight);
    try {
      // A real asynchronous transport delay catches overlapping polling chains.
      await new Promise(resolve => setTimeout(resolve, 80));
      if (preview && Date.now() >= readyAt) service.session.feedback = { ...preview, status: 'accepted', result: { accepted: true, code: 'accepted', message: 'Current preview ready.', checks: [], evidence: {}, verifier_version: 'fixture-v1' } };
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(service.session) });
    } finally { inFlight--; }
  });
  await page.route('**/api/sessions/session-1234-5678/captures', async route => {
    const raw = route.request().postData() || '';
    expect(raw).toContain('"slot":"preview"');
    previewPosts.push(Date.now()); readyAt = Date.now() + 900;
    preview = { id: `preview-${previewPosts.length}`, slot: 'preview', status: 'queued', epoch: service.session.epoch, revision: service.session.revision, frames: [], result: null, error: null };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ capture: preview, session: service.session }) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByLabel('Live guidance')).toBeEnabled();
  await page.getByLabel('Live guidance').check();
  await expect(page.getByText('Current preview ready.', { exact: true })).toBeVisible({ timeout: 5000 });
  const fastReads = snapshots.filter(time => time >= previewPosts[0]);
  expect(fastReads.length).toBeGreaterThanOrEqual(2);
  expect(fastReads[0] - previewPosts[0]).toBeLessThan(550);
  for (let index = 1; index < fastReads.length; index++) expect(fastReads[index] - fastReads[index - 1]).toBeGreaterThanOrEqual(230);
  expect(maximumInFlight).toBe(1);
  await page.getByLabel('Live guidance').uncheck();
  const idleStart = Date.now();
  await page.waitForTimeout(1100);
  expect(snapshots.filter(time => time > idleStart + 80)).toHaveLength(0);
  await expect.poll(() => snapshots.some(time => time > idleStart + 1200), { timeout: 1400 }).toBe(true);
  expect(previewPosts).toHaveLength(1);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  const hiddenReads = snapshots.length;
  await page.waitForTimeout(1700);
  expect(snapshots).toHaveLength(hiddenReads);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => snapshots.length, { timeout: 600 }).toBeGreaterThan(hiddenReads);
  await page.getByRole('button', { name: 'New collection', exact: false }).click();
  const afterLeave = snapshots.length;
  await page.waitForTimeout(1700);
  expect(snapshots).toHaveLength(afterLeave);
  expect(previewPosts).toHaveLength(1);
  expect(maximumInFlight).toBe(1);
});

test('appearance-only uncertainty offers review of the saved pair without approving it', async ({ page }) => {
  const service = await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  const first = structuredClone(service.session.baseline);
  service.session.latest_capture = { ...first, id: 'appearance-second', slot: 'counterpart', status: 'rejected', result: { ...first.result, accepted: false, code: 'appearance_uncertain', outcome: 'unknown', message: 'Appearance is inconclusive.', checks: [{ name: 'reference_position', passed: true, outcome: 'pass' }, { name: 'background_registration:0', passed: true, outcome: 'pass' }, { name: 'appearance_continuity:bottle:0', passed: false, outcome: 'unknown' }] } };
  await expect(page.getByRole('button', { name: 'Review these two photos' })).toBeVisible();
  await expect(page.getByRole('article', { name: 'Photo 2 needs review' })).toBeVisible();
  await expect(page.getByText('Both photos are saved. Appearance needs review.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Take another second photo instead' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save the complete pair' })).toHaveCount(0);
  expect(service.session.baseline).toEqual(first);
  expect(service.session.counterpart).toBeNull();
  expect(service.session.pair).toBeNull();
  await page.getByRole('button', { name: 'Review these two photos' }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/evidence/${service.session.id}\\?baseline=${first.id}&counterpart=appearance-second`));
});

test('appearance advice does not hide failed geometry or unresolved camera checks', async ({ page }) => {
  const service = await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  const first = service.session.baseline;
  const checks: any[] = [{ name: 'appearance_continuity:bottle:0', passed: false, outcome: 'unknown' }, { name: 'reference_position', passed: false, outcome: 'fail' }];
  service.session.latest_capture = { ...first, id: 'mixed-second', slot: 'counterpart', status: 'rejected', result: { ...first.result, accepted: false, code: 'reference_moved', outcome: 'fail', message: 'The cup moved.', checks } };
  await expect(page.getByText('Keep the first. Fix this one.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review these two photos' })).toHaveCount(0);
  checks[1] = { name: 'background_registration:0', passed: false, outcome: 'unknown', code: 'registration_uncertain' };
  Object.assign(service.session.latest_capture.result, { code: 'registration_uncertain', outcome: 'unknown' });
  await expect(page.getByText('Show more fixed background detail.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review these two photos' })).toHaveCount(0);
});
