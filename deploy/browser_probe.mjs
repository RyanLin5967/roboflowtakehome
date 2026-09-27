// Real Chromium against the compiled, authenticated disposable container stack.
// Only the private harness directory path is supplied; tokens are read locally.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const directory = process.argv[2];
if (!directory) throw new Error('Supply the private disposable deployment directory.');
const configuration = await fs.readFile(path.join(directory, 'private/runtime.env'), 'utf8');
const token = configuration.match(/^COUNTERPART_OPERATOR_TOKEN=(.+)$/m)?.[1];
assert(token && token.length >= 32);
const browser = await chromium.launch({ headless: true });
const errors = [];
const assets = [];
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, acceptDownloads: true });
const page = await context.newPage();
page.on('pageerror', () => errors.push('pageerror'));
page.on('console', message => { if (message.type() === 'error') errors.push('console_error'); });
page.on('response', response => {
  const pathname = new URL(response.url()).pathname;
  if (pathname.startsWith('/assets/')) assets.push({ pathname, status: response.status(), type: response.headers()['content-type'] });
});
try {
  await page.goto('http://127.0.0.1:18080/#/workspace');
  await expect(page.getByLabel('Operator access token')).toBeVisible();
  assert.equal(await page.getByRole('button', { name: 'New collection' }).count(), 0);
  await page.getByLabel('Operator access token').fill(token);
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByRole('button', { name: 'New collection' })).toBeVisible();
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
  const zip = await fs.readFile(await download.path());
  assert.equal(zip.subarray(0, 2).toString(), 'PK');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(overflow, false);
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.equal(storage.includes(token), false);
  assert.equal((await page.locator('body').innerText()).includes(token), false);
  assert(assets.some(asset => asset.pathname.endsWith('.js') && /javascript/.test(asset.type)));
  assert(assets.some(asset => asset.pathname.endsWith('.css') && asset.type.includes('text/css')));
  assert(assets.every(asset => asset.status === 200));
  assert.deepEqual(errors, []);
  await fs.mkdir('artifacts/deployment', { recursive: true });
  await page.screenshot({ path: 'artifacts/deployment/container-mobile-release.png', fullPage: true });
  const evidence = {
    browser: 'Chromium', viewport: { width: 390, height: 844 }, real_compiled_assets: true,
    anonymous_login_gate: true, operator_signin: true, existing_collection_opened: true,
    saved_images_rendered: true, release_download_bytes: zip.length, viewport_overflow: false,
    page_errors: errors.length, correct_script_and_style_mime: true, token_absent_from_browser_storage: true,
  };
  const reportPath = 'research/deployment_probe.json';
  const report = JSON.parse(await fs.readFile(reportPath, 'utf8'));
  report.browser = evidence;
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await context.close();
  await browser.close();
}
