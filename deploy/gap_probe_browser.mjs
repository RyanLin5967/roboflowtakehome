// Actual Chromium against the compiled HTTPS container stack. A temporary
// certificate is pinned only in this browser process; OS trust is untouched.
// The fake device proves HTTPS/media plumbing, not real capture/model quality.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const directory = process.argv[2];
assert(directory, 'Supply the private harness directory.');
const configuration = await fs.readFile(path.join(directory, 'private/runtime.env'), 'utf8');
const token = configuration.match(/^COUNTERPART_OPERATOR_TOKEN=(.+)$/m)?.[1];
const spki = (await fs.readFile(path.join(directory, 'private/spki'), 'utf8')).trim();
assert(token && token.length >= 32);
assert(/^[A-Za-z0-9+/]{43}=$/.test(spki));
const origin = 'https://counterpart.test:18443';
const browser = await chromium.launch({
  headless: true,
  args: [
    '--host-resolver-rules=MAP counterpart.test 127.0.0.1',
    `--ignore-certificate-errors-spki-list=${spki}`,
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
    '--no-proxy-server',
  ],
});
const errors = [];
const assets = [];
const context = await browser.newContext({
  viewport: { width: 390, height: 844 }, acceptDownloads: true,
  ignoreHTTPSErrors: false,
});
await context.grantPermissions(['camera'], { origin });
const page = await context.newPage();
page.on('pageerror', () => errors.push('pageerror'));
page.on('console', message => { if (message.type() === 'error') errors.push('console_error'); });
page.on('response', response => {
  const pathname = new URL(response.url()).pathname;
  if (pathname.startsWith('/assets/')) assets.push({
    pathname, status: response.status(), type: response.headers()['content-type'],
  });
});
try {
  const response = await page.goto(`${origin}/#/workspace`);
  assert.equal(response.status(), 200);
  const headers = await response.allHeaders();
  assert.equal(headers['permissions-policy'], 'camera=(self), microphone=(), geolocation=()');
  assert.equal(headers['x-content-type-options'], 'nosniff');
  assert.equal(headers['referrer-policy'], 'no-referrer');
  assert(headers['strict-transport-security'].includes('max-age='));
  await expect(page.getByLabel('Operator access token')).toBeVisible();
  assert.equal(await page.getByRole('button', { name: 'New collection' }).count(), 0);
  const camera = await page.evaluate(async () => {
    if (!isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      return { secure_context: isSecureContext, camera_available: false };
    }
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const track = stream.getVideoTracks()[0];
    const settings = track.getSettings();
    const result = {
      secure_context: isSecureContext, camera_available: true,
      track_live: track.readyState === 'live', width: settings.width, height: settings.height,
      audio_tracks: stream.getAudioTracks().length,
    };
    stream.getTracks().forEach(item => item.stop());
    return result;
  });
  assert(camera.secure_context && camera.camera_available && camera.track_live);
  assert(camera.width > 0 && camera.height > 0 && camera.audio_tracks === 0);
  await page.getByLabel('Operator access token').fill(token);
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByRole('button', { name: 'New collection' })).toBeVisible();
  const cookies = await context.cookies(origin);
  const operator = cookies.find(item => item.name === 'counterpart_operator');
  assert(operator?.secure && operator.httpOnly && operator.sameSite === 'Strict');
  assert.equal(await page.evaluate(() => document.cookie.includes('counterpart_operator')), false);
  await page.getByRole('button', { name: /Disposable container acceptance probe/ }).first().click();
  await expect(page.getByRole('heading', { name: 'Disposable container acceptance probe.' })).toBeVisible();
  await page.getByRole('button', { name: 'Review evidence' }).click();
  await expect(page.getByRole('img', { name: 'First saved capture', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Second saved capture', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: /Releases \(1\)/ }).click();
  const downloadReady = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download dataset ZIP' }).click();
  const download = await downloadReady;
  assert.equal(await download.failure(), null);
  const archive = await fs.readFile(await download.path());
  assert.equal(archive.subarray(0, 2).toString(), 'PK');
  await fs.mkdir('artifacts/deployment', { recursive: true });
  await page.screenshot({ path: 'artifacts/deployment/https-gap-mobile-release.png', fullPage: true });
  await page.getByRole('tab', { name: /Sessions \(1\)/ }).click();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Saved evidence decisions' })).toBeVisible();
  await page.getByText('Decision history · 1 revision', { exact: true }).click();
  await expect(page.getByText('Comparison outcome: Unknown', { exact: true })).toBeVisible();
  await expect(page.getByText(/Its full comparison was not evaluated\./)).toBeVisible();
  await page.getByText('Revised baseline checks', { exact: true }).click();
  await expect(page.getByText(/Background matches cover too little/).first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(overflow, false);
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.equal(storage.includes(token), false);
  assert.equal((await page.locator('body').innerText()).includes(token), false);
  assert(assets.some(asset => asset.pathname.endsWith('.js') && /javascript/.test(asset.type)));
  assert(assets.some(asset => asset.pathname.endsWith('.css') && asset.type.includes('text/css')));
  assert(assets.every(asset => asset.status === 200));
  assert.deepEqual(errors, []);
  await page.screenshot({ path: 'artifacts/deployment/https-gap-mobile-comparison.png', fullPage: true });
  const evidence = {
    browser: 'Chromium', viewport: { width: 390, height: 844 }, real_compiled_assets: true,
    browser_origin: origin, ephemeral_certificate_spki_pinned: true,
    global_certificate_errors_ignored: false, host_os_trust_modified: false,
    anonymous_login_gate: true, secure_httponly_strict_cookie: true,
    security_headers_present: true, fake_camera: camera, physical_camera_test: false,
    existing_collection_opened: true, saved_images_rendered: true,
    comparison_uncertainty_explained: true, actual_revised_baseline_checks_rendered: true,
    release_download_bytes: archive.length, viewport_overflow: false, page_errors: errors.length,
    correct_script_and_style_mime: true, token_absent_from_browser_storage: true,
  };
  const reportPath = 'research/gap_deployment_probe.json';
  const report = JSON.parse(await fs.readFile(reportPath, 'utf8'));
  report.browser = evidence;
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await context.close();
  await browser.close();
}
