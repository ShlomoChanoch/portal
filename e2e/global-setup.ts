const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Fail fast (and readably) when the dev stack is not up. A real E2E run needs the
 * SPA *and* the API; the runner is only needed by @real specs.
 */
export default async function globalSetup(): Promise<void> {
  const problems: string[] = [];
  if (!(await reachable(BASE_URL))) {
    problems.push(`frontend not reachable at ${BASE_URL}`);
  }
  if (!(await reachable(`${BASE_URL}/api/live`))) {
    problems.push(`API not reachable through ${BASE_URL}/api/live`);
  }
  if (problems.length > 0) {
    throw new Error(
      `Portal E2E stack is not running:\n  - ${problems.join('\n  - ')}\n\n` +
        `Start it first (from the repo root):\n` +
        `  make dev          # api + runner + frontend\n` +
        `  make dev-podman   # same, with the real BuildKit/Podman pipeline (@real specs)\n`,
    );
  }
}
