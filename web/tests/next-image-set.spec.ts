import { expect, test, type Page } from '@playwright/test';

// Protocol/UI fixtures; no model-quality claims or provider calls.
async function savedSet(page: Page, options: { loseResponse?: boolean; refuse?: boolean } = {}) {
  const sourceId = 'saved-set-1234';
  const childId = 'next-set-5678';
  const frame = { asset_id: 'frame-1', t_ms: 0, width: 720, height: 440 };
  const capture = (slot: string) => ({ id: slot, slot, status: 'accepted', revision: 1, epoch: 0,
    frames: [frame], result: { accepted: true, code: 'accepted', message: 'Checks passed.', checks: [] }, error: null });
  const source = { id: sourceId, recipe: 'spatial', mode: 'replay', object_a: 'book', object_b: 'cup',
    campaign_id: 'collection-1', revision: 3, epoch: 0, state: 'published', recovery_required: false,
    baseline: capture('baseline'), counterpart: capture('counterpart'), latest_capture: capture('counterpart'),
    feedback: null, pair: { id: 'pair-1' }, review: { status: 'pending', version: 0 }, events: [],
    recipe_snapshot: { protected_objects: ['apple'] } };
  const child = { ...source, id: childId, state: 'collecting', revision: 0, epoch: 0,
    baseline: null, counterpart: null, latest_capture: null, pair: null, review: undefined };
  const requests: Array<{ idempotency_key: string }> = [];
  await page.addInitScript(({ id }) => {
    if (!localStorage.getItem('counterpart.session.v1')) {
      localStorage.setItem('counterpart.session.v1', JSON.stringify({ id, token: 'fixture-source-capability' }));
    }
  }, { id: sourceId });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/health') return json({ status: 'ok', live_enabled: true });
    if (path === `/api/sessions/${sourceId}/next`) {
      expect(route.request().headers().authorization).toBe('Bearer fixture-source-capability');
      requests.push(route.request().postDataJSON());
      if (options.refuse) return json({ detail: { code: 'invite_exhausted', message: 'This invitation has reached its session limit.' } }, 409);
      if (options.loseResponse && requests.length === 1) return route.abort('failed');
      return json({ token: 'fixture-child-capability', session: child });
    }
    if (path.includes('/assets/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="440"><rect width="720" height="440" fill="#e9e5d9"/></svg>' });
    if (path.endsWith('/archive')) return route.fulfill({ contentType: 'application/zip', body: 'fixture archive' });
    if (path.endsWith('/manifest')) return json({ id: 'pair-1' });
    if (path === `/api/sessions/${sourceId}` || path === `/api/sessions/${sourceId}/resume`) return json(source);
    if (path === `/api/sessions/${childId}` || path === `/api/sessions/${childId}/resume`) return json(child);
    return json({ detail: `Unexpected fixture request ${path}` }, 500);
  });
  return { source, child, requests };
}

test('a saved collection offers another set immediately and keeps both sets accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const service = await savedSet(page);
  const original = structuredClone(service.source);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Image set saved.' })).toBeVisible();
  await expect(page.getByText(/Awaiting operator review/)).toHaveCount(0);
  const next = page.getByRole('button', { name: 'Add another image set' });
  await expect(next).toBeEnabled();
  await expect(next).toBeInViewport();
  await expect(page.getByRole('button', { name: 'Download complete pair' })).toBeEnabled();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download complete pair' }).click();
  expect((await downloaded).suggestedFilename()).toMatch(/\.zip$/);
  await next.click();
  await expect(page.getByRole('heading', { name: 'Place the book to the left of the cup.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  await expect(page.getByText('Also keep apple visible and still.', { exact: false })).toBeVisible();
  expect(service.requests).toHaveLength(1);
  expect(service.source).toEqual(original);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByRole('link', { name: 'Your sessions' }).click();
  const cards = page.locator('.session-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.filter({ hasText: 'Complete pair' })).toContainText('saved-se');
  await expect(cards.filter({ hasText: 'In progress' })).toContainText('next-set');
  await cards.filter({ hasText: 'Complete pair' }).getByRole('button', { name: 'Open session' }).click();
  await expect(page.getByRole('heading', { name: 'Image set saved.' })).toBeVisible();
});

test('a lost response survives reload and retries the same next-set identity', async ({ page }) => {
  const service = await savedSet(page, { loseResponse: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Add another image set' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Image set saved.' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Add another image set' }).click();
  await expect(page.getByRole('button', { name: 'Capture the first image' })).toBeEnabled();
  expect(service.requests).toHaveLength(2);
  expect(service.requests[1]).toEqual(service.requests[0]);
  expect(await page.evaluate(() => localStorage.getItem('counterpart.next-set.v1.saved-set-1234'))).toBeNull();
});

test('a collection limit leaves the original saved set downloadable', async ({ page }) => {
  const service = await savedSet(page, { refuse: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Add another image set' }).click();
  await expect(page.getByRole('alert')).toContainText('This invitation has reached its session limit.');
  await expect(page.getByRole('heading', { name: 'Image set saved.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download complete pair' })).toBeEnabled();
  expect(service.source.state).toBe('published');
});
