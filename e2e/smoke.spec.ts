import { expect, test } from '@playwright/test';

import { deployHeading, repoInput } from './helpers';

test.describe('shell, routing & deploy surface', () => {
  test('the root route redirects to the deploy screen', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/deploy$/);
    await expect(deployHeading(page)).toBeVisible();
  });

  test('an unknown route falls back to the deploy screen', async ({ page }) => {
    await page.goto('/#/no-such-page');
    await expect(page).toHaveURL(/#\/deploy$/);
    await expect(deployHeading(page)).toBeVisible();
  });

  test('the repo form is available with no sign-in step at all', async ({ page }) => {
    await page.goto('/#/deploy');
    await expect(deployHeading(page)).toBeVisible();
    await expect(repoInput(page)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Deploy', exact: true })).toBeVisible();
  });

  test('the repo form stays locked until a repo is given', async ({ page }) => {
    await page.goto('/#/deploy');
    await expect(deployHeading(page)).toBeVisible();

    const input = repoInput(page);
    const deploy = page.getByRole('button', { name: 'Deploy', exact: true });
    await expect(input).toBeVisible();
    /* Empty repo → Deploy stays disabled; any repo URL enables it. */
    await expect(deploy).toBeDisabled();
    await input.fill('demo');
    await expect(deploy).toBeEnabled();
  });

  test('primary nav reaches Your Deployments', async ({ page }) => {
    await page.goto('/#/deploy');
    await page
      .getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Your Deployments' })
      .click();
    await expect(page).toHaveURL(/#\/deployments$/);
  });

  test('keyboard primitives: `d` opens deployments and `/` returns to deploy', async ({ page }) => {
    await page.goto('/#/deploy');
    /* Nothing auto-waits for a keypress, so gate on the shell being mounted
     * (this used to be the implicit job of the removed sign-in wait). */
    await expect(deployHeading(page)).toBeVisible();

    await page.keyboard.press('d');
    await expect(page).toHaveURL(/#\/deployments$/);

    await page.keyboard.press('/');
    await expect(page).toHaveURL(/#\/deploy$/);
    await expect(repoInput(page)).toBeFocused();
  });

  test('keyboard primitive: the `g d` chord opens deployments, not the input', async ({ page }) => {
    await page.goto('/#/deploy');

    /* The chord is inert while a form field has focus (typing guard). */
    await repoInput(page).blur();
    await page.keyboard.press('g');
    await page.keyboard.press('d');
    await expect(page).toHaveURL(/#\/deployments$/);
  });
});
