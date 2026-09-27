// Actual compiled UI/API/PostgreSQL/worker. Every image/observation is an
// explicitly labeled fixture; scripted review is not human-quality evidence.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const base = process.env.COUNTERPART_BASE_URL || 'http://127.0.0.1:8080';
const seeded = JSON.parse(execFileSync('uv', ['run', 'python', 'scripts/gap_browser_seed.py'], {
  cwd: root, encoding: 'utf8', timeout: 100000,
}));
const uncertain = seeded.sessions.find(item => item.kind === 'uncertain');
const knownFailure = seeded.sessions.find(item => item.kind === 'known_failure');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const output = new URL('../artifacts/gap-browser/', import.meta.url);
await mkdir(output, { recursive: true });

async function api(path) {
  const response = await context.request.get(`${base}/api/operator${path}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function inspect(saved) {
  await page.getByRole('tab', { name: /Sessions/ }).click();
  const row = page.getByRole('row').filter({ hasText: saved.session_id.slice(0, 8) });
  await row.getByRole('button', { name: 'Inspect', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Saved evidence decisions' })).toBeVisible();
}
async function choosePair(saved) {
  const panel = page.locator('.adjudication-panel');
  await panel.locator('summary').click();
  await panel.getByLabel('First observation for human review').selectOption(saved.baseline_id);
  await panel.getByLabel('Counterpart observation for human review').selectOption(saved.counterpart_id);
  await expect(panel.locator('.review-pair-grid img')).toHaveCount(2);
  await panel.getByLabel('First observation label').selectOption('left');
  await panel.getByLabel('Counterpart label').selectOption('right');
  for (const checkbox of await panel.getByRole('checkbox').all()) await checkbox.check();
  await panel.getByLabel('Human decision note').fill(
    'Scripted fixture review: original generated images show the bottle changing sides with the cup fixed. '
    + 'The deliberately low-confidence fixture observation is separate from the original pixels.',
  );
  return panel;
}

try {
  await page.goto(`${base}/#/workspace`);
  await page.getByRole('button', { name: new RegExp(seeded.name) }).click();
  await page.getByRole('tab', { name: 'Invite collectors' }).click();
  await page.getByLabel('Scene group', { exact: true }).selectOption(seeded.group_id);
  await expect(page.getByLabel('Scene group', { exact: true })).toHaveValue(seeded.group_id);

  await inspect(uncertain);
  const before = await api(`/sessions/${uncertain.session_id}/evidence`);
  const original = before.captures.find(item => item.capture.id === uncertain.counterpart_id).capture;
  assert.equal(original.result.accepted, false);
  assert.equal(original.result.code, 'low_confidence');
  const card = page.locator('.evidence-decision-list > article').filter({ hasText: uncertain.counterpart_id.slice(0, 8) });
  await card.locator('.decision-history > summary').click();
  await card.getByLabel(`Verifier version for ${uncertain.counterpart_id}`).selectOption('2.0.0');
  await card.getByRole('button', { name: 'Append verification revision', exact: true }).click();
  await expect(card.getByText('REVISION 1', { exact: true })).toBeVisible();
  const revised = await api(`/sessions/${uncertain.session_id}/evidence`);
  const revisedCapture = revised.captures.find(item => item.capture.id === uncertain.counterpart_id);
  assert.deepEqual(revisedCapture.capture, original);
  assert.equal(revisedCapture.revisions.length, 1);
  assert.equal(revisedCapture.revisions[0].recipe_snapshot.recipe_version, '2.0.0');

  const panel = await choosePair(uncertain);
  await expect(panel.getByRole('button', { name: 'Record human decision', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: 'Record human decision', exact: true }).click();
  await expect(page.getByText(/Human decision recorded\. The new pair awaits ordinary pair review/)).toBeVisible();
  await page.screenshot({ path: new URL('human-decision.png', output).pathname, fullPage: true });
  const adjudicated = await api(`/sessions/${uncertain.session_id}/evidence`);
  assert.deepEqual(adjudicated.captures.find(item => item.capture.id === uncertain.counterpart_id).capture, original);
  assert.equal(adjudicated.adjudications.length, 1);

  // A human confirmation cannot turn a known failed relation into valid data.
  await inspect(knownFailure);
  const denied = await choosePair(knownFailure);
  await expect(denied.getByRole('button', { name: 'Record human decision', exact: true })).toBeDisabled();

  await page.getByRole('tab', { name: /Review pairs/ }).click();
  const pairList = await api(`/campaigns/${seeded.campaign_id}/pairs`);
  assert.equal(pairList.pairs.length, 2);
  for (const pair of pairList.pairs) {
    await page.locator('.pair-row').filter({ hasText: pair.id.slice(0, 8) })
      .getByRole('button', { name: /^Review evidence/ }).click();
    await page.getByRole('button', { name: /^Approve pair/ }).click();
    await expect(page.locator('.review-panel')).toHaveCount(0);
  }
  await page.getByRole('button', { name: 'Select approved', exact: true }).click();
  await page.getByRole('checkbox', { name: /Create a labeled test release/ }).check();
  await page.getByRole('button', { name: /Create dataset release/ }).click();
  await expect(page.getByRole('button', { name: 'Download dataset ZIP', exact: true })).toBeVisible();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download dataset ZIP', exact: true }).click();
  const download = await downloaded;
  assert.equal(await download.failure(), null);
  await download.saveAs(new URL('dataset.zip', output).pathname);
  const releases = await api(`/campaigns/${seeded.campaign_id}/releases`);
  const release = (releases.releases || releases)[0];
  const manifest = await api(`/releases/${release.id}/manifest`);
  assert.equal(manifest.pair_count, 2);
  const memberships = manifest.pairs.map(pair => pair.manifest || pair);
  assert.equal(new Set(memberships.map(pair => pair.split_group)).size, 1);
  assert.equal(new Set(memberships.map(pair => pair.split)).size, 1);

  // Private-provider controls must remain unavailable in a replay-only release.
  const deliverySummary = await api(`/releases/${release.id}/roboflow`);
  assert.equal(deliverySummary.generation_enabled, false);
  assert.equal(deliverySummary.snapshots.length, 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: new URL('mobile-release.png', output).pathname, fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  assert.equal(overflow, false);
  assert.deepEqual(errors, []);
  const report = {
    evidence_kind: 'fixture_replay_with_scripted_human_review', cloud_calls: 0,
    actual_ui_api_database_worker: true, scene_group_selected: true,
    saved_observations_reverified: true, original_decision_unchanged: true,
    human_uncertainty_decision: true, known_failure_cannot_be_approved: true,
    human_pair_independently_reviewed_and_released: true,
    two_related_sessions_share_split: true, dataset_zip_downloaded: true,
    private_delivery_disabled: true, mobile_overflow: overflow, browser_errors: errors,
  };
  await writeFile(new URL('report.json', output), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  await context.close();
  await browser.close();
}
