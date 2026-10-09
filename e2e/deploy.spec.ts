import { expect, test } from '@playwright/test';

import { BASE_URL, repoInput } from './helpers';

/**
 * Opt-in end-to-end proof of the whole pipeline (TODO 13 / RUNBOOK §2a): deploy
 * the offline `demo` repo, watch it reach running with a real OCI image, then
 * stop and delete it. Needs a runner wired to BuildKit + rootless Podman, so it
 * is excluded from the default suite and run with `npm run test:e2e:real`.
 *
 * The public URL points at the portal.dev dev domain, which the test host does
 * not resolve; serving is asserted through the API's public_url + image_ref.
 */
test.describe('@real deploy lifecycle', () => {
  test('a demo Deployment builds, runs, stops and deletes', async ({ page }) => {
    test.setTimeout(8 * 60_000);

    await page.goto('/#/deploy');
    await repoInput(page).fill('demo');
    await page.getByRole('button', { name: 'Deploy', exact: true }).click();

    await expect(page).toHaveURL(/#\/deployments\/[^/]+$/, { timeout: 30_000 });
    const slug = page.url().split('/').pop() as string;

    try {
      await expect(page.getByRole('heading', { name: slug })).toBeVisible();

      /* submitted → building → starting → running (real builds take minutes). */
      await expect(page.getByText('Running', { exact: true })).toBeVisible({
        timeout: 6 * 60_000,
      });

      await expect(page.getByRole('link', { name: 'Open URL in a new tab' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Copy URL' })).toBeVisible();

      const res = await page.request.get(`${BASE_URL}/api/deployments/${slug}`);
      expect(res.ok()).toBeTruthy();
      const { deployment } = (await res.json()) as {
        deployment: { image_ref: string | null; public_url: string | null; mapped_port: number | null };
      };
      expect(deployment.image_ref, 'the real pipeline built an OCI image').toBeTruthy();
      expect(deployment.public_url, 'a running Deployment has a public URL').toBeTruthy();
      expect(deployment.mapped_port, 'a running Deployment has a mapped port').toBeTruthy();

      /* Stop, then delete (delete is only offered once no longer active). */
      await page.getByRole('button', { name: 'Stop', exact: true }).click();
      await expect(page.getByText('Stopped', { exact: true })).toBeVisible({ timeout: 60_000 });

      await page.getByRole('button', { name: `Delete ${slug}` }).click();
      await page.getByRole('button', { name: 'Delete permanently' }).click();
      await expect(page).toHaveURL(/#\/deployments$/, { timeout: 30_000 });
    } finally {
      /* Best-effort cleanup so a failed run does not leak a quota slot. */
      await page.request
        .post(`${BASE_URL}/api/deployments/${slug}/stop`)
        .catch(() => undefined);
      await page.request
        .delete(`${BASE_URL}/api/deployments/${slug}`)
        .catch(() => undefined);
    }
  });
});
