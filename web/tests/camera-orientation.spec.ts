import { expect, test, type Page } from '@playwright/test';

// Real browser video/canvas/JPEG transport, deterministic colored pixels only.
// These tests exercise orientation; they do not invoke a model or claim CV quality.
const sessionId = 'camera-orientation-session';
const orientationKey = `counterpart.camera-orientation.v1:${sessionId}`;

async function cameraFixture(page: Page, facing: 'user' | 'environment' | 'unspecified') {
  await page.addInitScript(({ facing }) => {
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement('canvas');
      canvas.width = 320; canvas.height = 180;
      const context = canvas.getContext('2d')!;
      let swapped = false;
      const paint = () => {
        context.fillStyle = swapped ? '#0000ff' : '#ff0000'; context.fillRect(0, 0, 160, 180);
        context.fillStyle = swapped ? '#ff0000' : '#0000ff'; context.fillRect(160, 0, 160, 180);
      };
      window.addEventListener('test-camera-scene-change', () => { swapped = true; paint(); });
      paint();
      const stream = canvas.captureStream(30);
      const track = stream.getVideoTracks()[0];
      const getSettings = track.getSettings.bind(track);
      track.getSettings = () => ({ ...getSettings(), ...(facing === 'unspecified' ? {} : { facingMode: facing }) });
      const timer = setInterval(paint, 30);
      const stop = track.stop.bind(track);
      track.stop = () => { clearInterval(timer); stop(); };
      return stream;
    };
  }, { facing });
}

async function serviceFixture(page: Page, options: { legacy?: boolean; failFirstUpload?: boolean } = {}) {
  const uploads: Buffer[][] = [];
  const identities: string[] = [];
  const capture = (slot = 'baseline') => ({ id: `capture-${uploads.length}`, slot, status: 'accepted', revision: uploads.length, epoch: 0, created_at: '', frames: [{ asset_id: 'fixture-image', t_ms: 0, width: 320, height: 180 }], result: { accepted: true, code: 'accepted', message: 'Fixture relation accepted.', checks: [], evidence: {}, verifier_version: 'fixture-v1' }, error: null });
  const session: any = { id: sessionId, recipe: 'spatial', mode: 'live', object_a: 'bottle', object_b: 'cup', revision: 0, epoch: 0, state: 'collecting', recovery_required: false, baseline: options.legacy ? capture() : null, counterpart: null, latest_capture: null, feedback: null, pair: null, events: [] };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (data: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/api/health') return json({ status: 'ok', live_enabled: true, provider: 'fixture' });
    if (path === '/api/sessions') { Object.assign(session, route.request().postDataJSON()); return json({ token: 'test-only', session }); }
    if (path.endsWith('/resume')) { session.epoch++; session.recovery_required = !!session.baseline; return json(session); }
    if (path.endsWith('/captures')) {
      // Inspect actual multipart JPEG bytes, after the browser serialized FormData.
      const body = route.request().postDataBuffer()!;
      const images: Buffer[] = [];
      let offset = 0;
      while (true) {
        const start = body.indexOf(Buffer.from([0xff, 0xd8]), offset);
        if (start < 0) break;
        const end = body.indexOf(Buffer.from([0xff, 0xd9]), start + 2);
        expect(end).toBeGreaterThan(start);
        images.push(body.subarray(start, end + 2)); offset = end + 2;
      }
      uploads.push(images);
      identities.push(body.toString().match(/"idempotency_key":"([^"]+)"/)?.[1] ?? 'missing');
      if (options.failFirstUpload && uploads.length === 1) return route.abort('failed');
      const slot = body.toString().match(/"slot":"([^"]+)"/)?.[1] ?? 'baseline';
      const saved = capture(slot);
      if (slot === 'baseline') session.baseline = saved;
      if (slot === 'counterpart') session.counterpart = saved;
      session.latest_capture = saved; session.state = 'baseline_ready'; session.recovery_required = false;
      return json({ capture: saved, session });
    }
    if (path.includes('/assets/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="gray"/></svg>' });
    if (path === `/api/sessions/${sessionId}`) return json(session);
    return route.fulfill({ status: 500, body: `Unexpected fixture route: ${path}` });
  });
  return { uploads, identities, session };
}

async function start(page: Page, temporal = false) {
  await page.goto('/');
  if (temporal) await page.getByRole('button', { name: /Reverse arrival order/ }).click();
  await page.getByRole('button', { name: 'Use my camera' }).click();
  await page.getByRole('button', { name: 'Start a real capture' }).click();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByRole('button', { name: temporal ? 'Record first sequence' : 'Capture the first image' })).toBeEnabled();
}

async function expectPixels(page: Page, bytes: Buffer, mirrored: boolean, mime = 'image/jpeg') {
  const colors = await page.evaluate(async ({ data, mime }) => {
    const image = await createImageBitmap(await (await fetch(`data:${mime};base64,${data}`)).blob());
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0); image.close();
    return [.2, .8].map(x => Array.from(context.getImageData(Math.floor(canvas.width * x), Math.floor(canvas.height / 2), 1, 1).data));
  }, { data: bytes.toString('base64'), mime });
  const [left, right] = mirrored ? [2, 0] : [0, 2];
  expect(colors[0][left]).toBeGreaterThan(240); expect(colors[0][right]).toBeLessThan(15);
  expect(colors[1][right]).toBeGreaterThan(240); expect(colors[1][left]).toBeLessThan(15);
}

async function expectPreview(page: Page, mirrored: boolean) {
  const video = page.getByLabel('Live camera view');
  // Stored orientation is available before the reopened camera delivers a
  // frame. An opacity-zero video still has a screenshot bounding box, so its
  // screenshot can contain the cream page background until the callback runs.
  await expect(video).toHaveCSS('opacity', '1');
  await expect.poll(() => video.evaluate(element => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
  await expectPixels(page, await video.screenshot(), mirrored, 'image/png');
}

for (const facing of ['user', 'unspecified', 'environment'] as const) {
  test(`${facing} camera preview and uploaded JPEG have the same orientation`, async ({ page }) => {
    const service = await serviceFixture(page); await cameraFixture(page, facing); await start(page);
    const mirrored = facing !== 'environment';
    await expectPreview(page, mirrored);
    await page.getByRole('button', { name: 'Capture the first image' }).click();
    await expect.poll(() => service.uploads.length).toBe(1);
    expect(service.uploads[0]).toHaveLength(1); await expectPixels(page, service.uploads[0][0], mirrored);
    expect(await page.evaluate(key => localStorage.getItem(key), orientationKey)).toBe(mirrored ? 'mirrored' : 'original');
    await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeDisabled();
  });
}

test('explicit orientation survives reload and is locked after the first saved image', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user'); await start(page);
  await page.getByRole('checkbox', { name: 'Mirror camera' }).uncheck();
  await expectPreview(page, false);
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect.poll(() => service.uploads.length).toBe(1); await expectPixels(page, service.uploads[0][0], false);
  await page.reload(); await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeDisabled();
  await expectPreview(page, false);
  await page.getByRole('button', { name: 'Check current scene' }).click();
  await expect.poll(() => service.uploads.length).toBe(2); await expectPixels(page, service.uploads[1][0], false);
});

test('mirrored pending uploads retry exactly the same bytes after reload', async ({ page }) => {
  const service = await serviceFixture(page, { failFirstUpload: true }); await cameraFixture(page, 'user'); await start(page);
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByRole('button', { name: 'Retry saved capture' })).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: 'Retry saved capture' }).click();
  await expect.poll(() => service.uploads.length).toBe(2);
  expect(service.identities[0]).not.toBe('missing'); expect(service.identities[1]).toBe(service.identities[0]);
  expect(service.uploads[1][0].equals(service.uploads[0][0])).toBe(true);
  await expectPixels(page, service.uploads[1][0], true);
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeChecked();
  await expectPreview(page, true);
});

test('every temporal frame uses the preview orientation and cannot flip mid-recording', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user'); await start(page, true);
  await page.getByRole('button', { name: 'Record first sequence' }).click();
  await expect(page.getByLabel('Capture countdown')).toContainText('2');
  await expect(page.locator('.recording-chip')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Stop & check sequence' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeDisabled();
  await expect.poll(async () => Number((await page.locator('.recording-chip').innerText()).match(/\d+/)?.[0]), { timeout: 8000 }).toBeGreaterThanOrEqual(9);
  await page.getByRole('button', { name: 'Stop & check sequence' }).click();
  await expect.poll(() => service.uploads.length).toBe(1); expect(service.uploads[0].length).toBeGreaterThanOrEqual(8);
  for (const bytes of service.uploads[0]) await expectPixels(page, bytes, true);
});

test('Space counts down before photographing the current scene and works for both pair members', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user'); await start(page);
  await page.locator('body').click({ position: { x: 1, y: 1 } });
  const started = Date.now();
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Capture countdown')).toContainText('2');
  expect(service.uploads).toHaveLength(0);
  await expect(page.getByLabel('Capture countdown')).toContainText('1');
  expect(service.uploads).toHaveLength(0);
  await page.evaluate(() => window.dispatchEvent(new Event('test-camera-scene-change')));
  await expect.poll(() => service.uploads.length).toBe(1);
  expect(Date.now() - started).toBeGreaterThanOrEqual(2000);
  // Colors changed during the countdown: prove the photo wasn't taken on click.
  await expectPixels(page, service.uploads[0][0], false);
  await expect(page.getByRole('button', { name: 'Capture the second image' })).toBeEnabled();
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Capture countdown')).toContainText('2');
  expect(service.uploads).toHaveLength(1);
  await expect.poll(() => service.uploads.length).toBe(2);
});

test('cancel or hide the page during countdown without submitting a capture', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user'); await start(page);
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await page.getByRole('button', { name: 'Cancel countdown' }).click();
  await expect(page.getByLabel('Capture countdown')).toHaveCount(0);
  await page.waitForTimeout(2100);
  expect(service.uploads).toHaveLength(0);
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect(page.getByLabel('Capture countdown')).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.getByLabel('Capture countdown')).toHaveCount(0);
  await page.waitForTimeout(2100);
  expect(service.uploads).toHaveLength(0);
});

test('Space ignores text entry and key repeat, and stops an active recording', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user'); await start(page, true);
  await page.evaluate(() => {
    const input = document.createElement('input'); input.setAttribute('aria-label', 'Test text input'); document.body.append(input); input.focus();
  });
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Test text input')).toHaveValue(' ');
  await expect(page.getByLabel('Capture countdown')).toHaveCount(0);
  await page.getByLabel('Test text input').evaluate(input => input.remove());
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: true, bubbles: true, cancelable: true })));
  await expect(page.getByLabel('Capture countdown')).toHaveCount(0);
  await page.keyboard.press('Space');
  await expect(page.getByLabel('Capture countdown')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Stop & check sequence' })).toBeVisible();
  await expect.poll(async () => Number((await page.locator('.recording-chip').innerText()).match(/\d+/)?.[0]), { timeout: 8000 }).toBeGreaterThanOrEqual(9);
  await page.keyboard.press('Space');
  await expect.poll(() => service.uploads.length).toBe(1);
  expect(service.uploads[0].length).toBeGreaterThanOrEqual(8);
});

test('legacy first capture without orientation metadata retains original coordinates', async ({ page }) => {
  await serviceFixture(page, { legacy: true }); await cameraFixture(page, 'user');
  await page.addInitScript(id => localStorage.setItem('counterpart.session.v1', JSON.stringify({ id, token: 'test-only' })), sessionId);
  await page.goto('/'); await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeDisabled();
  await expectPreview(page, false);
  expect(await page.evaluate(key => localStorage.getItem(key), orientationKey)).toBe('original');
});

test('a rejected legacy attempt also locks coordinates because it can be reviewed later', async ({ page }) => {
  const service = await serviceFixture(page, { legacy: true }); await cameraFixture(page, 'user');
  service.session.latest_capture = { ...service.session.baseline, status: 'rejected', result: { accepted: false, outcome: 'unknown', code: 'camera_registration_unknown', message: 'Fixture background is inconclusive.', checks: [], evidence: {}, verifier_version: 'fixture-v1' } };
  service.session.baseline = null;
  await page.addInitScript(id => localStorage.setItem('counterpart.session.v1', JSON.stringify({ id, token: 'test-only' })), sessionId);
  await page.goto('/'); await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByText('Camera options', { exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Mirror camera' })).toBeDisabled();
  await expectPreview(page, false);
});

test('unavailable orientation storage cannot create an unrecoverable mirrored baseline', async ({ page }) => {
  const service = await serviceFixture(page); await cameraFixture(page, 'user');
  await page.addInitScript(() => {
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith('counterpart.camera-orientation.')) throw new DOMException('Fixture storage blocked', 'QuotaExceededError');
      setItem.call(this, key, value);
    };
  });
  await start(page);
  await expect(page.getByText('Browser storage is unavailable. Using the original camera view so saved captures keep a consistent orientation.')).toBeVisible();
  await expectPixels(page, await page.getByLabel('Live camera view').screenshot(), false, 'image/png');
  await page.getByRole('button', { name: 'Capture the first image' }).click();
  await expect.poll(() => service.uploads.length).toBe(1); await expectPixels(page, service.uploads[0][0], false);
});
