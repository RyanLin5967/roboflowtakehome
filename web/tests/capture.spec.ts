import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// These are browser/protocol fixtures, not CV model predictions or model-quality tests.
async function mockService(page: Page, options: { live?: boolean; failFirstUpload?: boolean; rejectStaleRetry?: boolean } = {}) {
  let counter = 0;
  let uploads = 0;
  const submitted: string[] = [];
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
  return { session, submitted };
}

test('replay preserves the first capture through rejection, recovery, and complete publication', async ({ page }) => {
  await mockService(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await expect(page.getByText('SIMULATION · TEST SCENES')).toBeVisible();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByText('FIRST CAPTURE SAVED')).toBeVisible();
  await page.getByRole('button', { name: 'Move the reference' }).click();
  await expect(page.getByText('Keep the first. Fix this one.')).toBeVisible();
  await expect(page.getByText('FIRST CAPTURE SAVED')).toBeVisible();
  await page.getByRole('button', { name: 'Evidence & session history' }).click();
  await page.getByRole('button', { name: 'Reconnect & check recovery' }).click();
  await expect(page.getByRole('button', { name: 'Check current scene' })).toBeVisible();
  await page.getByRole('button', { name: 'Check current scene' }).click();
  await page.getByRole('button', { name: 'Capture the counterpart' }).click();
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

test('camera denial is actionable and makes no capture request', async ({ page }) => {
  const service = await mockService(page, { live: true });
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Denied', 'NotAllowedError')); });
  await page.goto('/');
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.getByText(/Camera permission was denied/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeDisabled();
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
  await expect(page.getByText('FIRST CAPTURE SAVED')).toBeVisible();
  expect(service.submitted).toHaveLength(2);
  expect(service.submitted[0]).not.toBe('missing');
  expect(service.submitted[0]).toBe(service.submitted[1]);
});

test('phone layout fits the viewport and preserves the main action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockService(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Collect the difference/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByRole('button', { name: 'Open replay session' }).click();
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Capture the counterpart' })).toBeVisible();
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
  await expect(page.getByText('FIRST CAPTURE SAVED')).toBeVisible();
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
