import { expect, test, type Page } from '@playwright/test';


const SLUG = 'demo-app';

function deployment(over: Record<string, unknown> = {}) {
  return {
    id: SLUG,
    tenant_id: 1,
    repo_url: 'demo',
    port_override: null,
    created_at: '2026-01-01T00:00:00+00:00',
    status: 'running',
    reason: null,
    phase: 'serving',
    mapped_port: 41000,
    public_url: `http://${SLUG}.portal.dev`,
    image_ref: `localhost/portal/${SLUG}:latest`,
    idle_stop_at: null,
    last_activity_at: null,
    updated_at: new Date().toISOString(),
    secrets: [],
    ...over,
  };
}

/** Mock the single-deployment GET (any method) plus both log feeds. */
async function mockBackend(page: Page, over: Record<string, unknown> = {}) {
  const body = deployment(over);
  await page.route(/\/api\/deployments\/[^/?]+$/, (route) =>
    route.fulfill({ json: { deployment: body } }),
  );
  await page.route(/\/api\/deployments\/.*\/logs(\?|$)/, (route) =>
    route.fulfill({
      json: {
        lines: [{ ts: '2026-01-01T00:00:00+00:00', line: 'a log line' }],
        next_offset: 1,
        total: 1,
        status: body.status,
      },
    }),
  );
  /* Keep a live SSE tab from erroring in the console. */
  await page.route(/\/api\/deployments\/.*\/logs\/stream/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  );
  return body;
}

test.describe('Deployment detail', () => {
  test('a running Deployment shows the URL payoff, Stop, and no Delete', async ({ page }) => {
    await mockBackend(page, { status: 'running' });

    await page.goto(`/#/deployments/${SLUG}`);
    await expect(page.getByRole('heading', { name: SLUG })).toBeVisible();
    await expect(page.getByText('Running', { exact: true })).toBeVisible();

    const pill = page.getByRole('link', { name: 'Open URL in a new tab' });
    await expect(pill).toBeVisible();
    await expect(pill).toHaveAttribute('href', `http://${SLUG}.portal.dev`);
    await expect(page.getByRole('button', { name: 'Copy URL' })).toBeVisible();

    await expect(page.getByRole('button', { name: `Redeploy ${SLUG}` })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: `Delete ${SLUG}` })).toHaveCount(0);
  });

  test('a failed Deployment shows its reason plus Start and Delete', async ({ page }) => {
    await mockBackend(page, { status: 'failed', reason: 'build_failed', public_url: null });

    await page.goto(`/#/deployments/${SLUG}`);
    await expect(page.getByText('Failed', { exact: true })).toBeVisible();
    await expect(page.getByText('build_failed')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: `Delete ${SLUG}` })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Open URL in a new tab' })).toHaveCount(0);
  });

  test('a stopped Deployment offers Start and Delete', async ({ page }) => {
    await mockBackend(page, { status: 'stopped', public_url: null });

    await page.goto(`/#/deployments/${SLUG}`);
    await expect(page.getByText('Stopped', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: `Delete ${SLUG}` })).toBeVisible();
  });

  test('the log tabs and Secrets tab render', async ({ page }) => {
    await mockBackend(page, { status: 'stopped', public_url: null });

    await page.goto(`/#/deployments/${SLUG}`);
    await expect(page.getByRole('log', { name: 'build output' })).toBeVisible();

    await page.getByRole('tab', { name: 'Run' }).click();
    await expect(page.getByRole('log', { name: 'run output' })).toBeVisible();

    await page.getByRole('tab', { name: 'Secrets' }).click();
    await expect(page.getByText('No secrets yet.')).toBeVisible();
  });

  test('Delete asks for confirmation and can be cancelled', async ({ page }) => {
    await mockBackend(page, { status: 'stopped', public_url: null });

    await page.goto(`/#/deployments/${SLUG}`);
    await page.getByRole('button', { name: `Delete ${SLUG}` }).click();

    await expect(page.getByText(`Delete ${SLUG}?`)).toBeVisible();
    await expect(page.getByText(/frees its quota slot/)).toBeVisible();
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText(`Delete ${SLUG}?`)).toHaveCount(0);
    await expect(page).toHaveURL(/#\/deployments\/demo-app$/);
  });
});
