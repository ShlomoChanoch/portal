import { expect, test, type Page } from '@playwright/test';


/** A Deployment row as /api/deployments returns it. */
function deployment(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    tenant_id: 1,
    repo_url: 'demo',
    port_override: null,
    created_at: '2026-01-01T00:00:00+00:00',
    status: 'running',
    reason: null,
    phase: 'serving',
    mapped_port: 41000,
    public_url: `http://${id}.portal.dev`,
    image_ref: `localhost/portal/${id}:latest`,
    idle_stop_at: null,
    last_activity_at: null,
    updated_at: new Date().toISOString(),
    ...over,
  };
}

/** Serve the list endpoint from an in-memory array, honouring offset/limit. */
async function mockList(page: Page, rows: Record<string, unknown>[]) {
  await page.route(/\/api\/deployments\?/, (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 25);
    return route.fulfill({
      json: { deployments: rows.slice(offset, offset + limit), total: rows.length, limit, offset },
    });
  });
}

test.describe('Your Deployments', () => {
  test('an empty account gets the empty state, which leads back to Deploy', async ({ page }) => {
    await mockList(page, []);

    await page.goto('/#/deployments');
    await expect(page.getByRole('heading', { name: 'Nothing deployed yet' })).toBeVisible();
    await page.getByRole('button', { name: 'Deploy your first app' }).click();
    await expect(page).toHaveURL(/#\/deploy$/);
  });

  test('rows render repo, status and port, and open the detail page', async ({ page }) => {
    await mockList(page, [
      deployment('demo-2', { status: 'building' }),
      deployment('demo-1', { status: 'stopped', mapped_port: null, public_url: null }),
    ]);
    await page.route(/\/api\/deployments\/demo-1$/, (route) =>
      route.fulfill({ json: { deployment: deployment('demo-1', { status: 'stopped' }) } }),
    );

    await page.goto('/#/deployments');
    await expect(page.getByRole('heading', { name: 'Your Deployments' })).toBeVisible();
    await expect(page.getByText('2 apps · newest first')).toBeVisible();
    await expect(page.getByRole('link', { name: 'demo-2' })).toBeVisible();

    /* Scope row assertions to the table: the DOM also holds a mobile card list. */
    const table = page.locator('table');
    const building = table.locator('tbody tr', { hasText: 'demo-2' });
    await expect(building.getByText('Building', { exact: true })).toBeVisible();
    await expect(building.getByText('41000')).toBeVisible();

    const stopped = table.locator('tbody tr', { hasText: 'demo-1' });
    await expect(stopped.getByText('Stopped', { exact: true })).toBeVisible();
    await expect(stopped.getByText('—')).toBeVisible();

    await page.getByRole('link', { name: 'demo-1' }).click();
    await expect(page).toHaveURL(/#\/deployments\/demo-1$/);
    await expect(page.getByRole('heading', { name: 'demo-1' })).toBeVisible();
  });

  test('Load more appends the next page and then retires', async ({ page }) => {
    const rows = Array.from({ length: 30 }, (_, i) =>
      deployment(`demo-${String(i).padStart(2, '0')}`),
    );
    await mockList(page, rows);

    await page.goto('/#/deployments');
    await expect(page.getByText('30 apps · newest first')).toBeVisible();
    await expect(page.getByRole('link', { name: 'demo-24' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'demo-25' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Load more' }).click();
    await expect(page.getByRole('link', { name: 'demo-25' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0);
  });
});
