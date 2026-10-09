import type { Page } from '@playwright/test';

export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';

export const DEPLOY_HEADING = 'Paste. Build. Run.';

export function repoInput(page: Page) {
  return page.getByLabel('Repo URL');
}

export function deployHeading(page: Page) {
  return page.getByRole('heading', { name: DEPLOY_HEADING });
}
