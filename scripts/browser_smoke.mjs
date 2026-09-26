// Actual browser + running API + separate worker. All observations are labeled replay fixtures.
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const base = process.env.COUNTERPART_BASE_URL || 'http://127.0.0.1:8080';
const output = new URL('../artifacts/', import.meta.url);
await mkdir(output, { recursive: true });
for (let attempt = 0; attempt < 40; attempt++) {
  try { if ((await fetch(`${base}/api/health`)).ok) break; } catch { /* Service starting. */ }
  if (attempt === 39) throw new Error('Capture API did not become ready.');
  await new Promise(resolve => setTimeout(resolve, 500));
}
const browser = await chromium.launch({ headless: true });
const scenarios = [];
try {
  for (const recipe of ['spatial', 'temporal']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    if (recipe === 'temporal') await page.getByRole('button', { name: /Reverse arrival order/ }).click();
    await page.getByRole('button', { name: 'Open replay session' }).click();
    const first = recipe === 'spatial' ? 'Capture the first image' : 'Record first sequence';
    const second = recipe === 'spatial' ? 'Capture the counterpart' : 'Record reverse sequence';
    await page.getByRole('button', { name: first, exact: true }).click();
    await expect(page.getByRole('button', { name: second, exact: true })).toBeEnabled({ timeout: 15000 });
    await page.getByRole('button', { name: recipe === 'spatial' ? 'Move the reference' : 'Wrong order', exact: true }).click();
    await expect(page.getByText('Keep the first. Fix this one.', { exact: true })).toBeVisible({ timeout: 15000 });
    await page.screenshot({ path: new URL(`${recipe}-repair.png`, output).pathname, fullPage: true });
    await page.reload();
    await expect(page.getByRole('button', { name: 'Check current scene', exact: true })).toBeEnabled({ timeout: 15000 });
    await page.getByRole('button', { name: 'Check current scene', exact: true }).click();
    await expect(page.getByRole('button', { name: second, exact: true })).toBeEnabled({ timeout: 15000 });
    await page.getByRole('button', { name: second, exact: true }).click();
    await expect(page.getByRole('button', { name: 'Save the complete pair', exact: true })).toBeEnabled({ timeout: 15000 });
    await page.getByRole('button', { name: 'Save the complete pair', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Download complete pair', exact: true })).toBeVisible({ timeout: 15000 });
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download complete pair', exact: true }).click();
    await (await downloading).saveAs(new URL(`${recipe}-browser-pair.zip`, output).pathname);
    if (recipe === 'temporal') {
      await page.getByRole('button', { name: 'Play sequence', exact: true }).first().click();
      await page.waitForTimeout(1500);
    }
    await page.screenshot({ path: new URL(`${recipe}-complete.png`, output).pathname, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: new URL(`${recipe}-mobile.png`, output).pathname, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    if (errors.length || overflow) throw new Error(JSON.stringify({ recipe, errors, overflow }));
    scenarios.push({ recipe, passed: true, rejected_wrong_attempt: true, reload_recovery: true,
      zip_downloaded: true, mobile_horizontal_overflow: false, browser_errors: errors });
    await page.close();
  }
  const report = { kind: 'Real browser/API/worker; synthetic observations, no model calls', scenarios };
  await writeFile(new URL('browser_smoke.json', output), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
