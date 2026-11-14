import { afterEach, describe, expect, it, vi } from 'vitest';

describe('dashboardSocketUrl（#3970 同源守卫）', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('VITE_API_BASE_URL 为空且非 localhost 时走相对路径 /dashboard（Nginx 同源）', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    vi.stubGlobal('location', {
      ...window.location,
      hostname: 'stp-ui-review.localhost',
      origin: 'http://stp-ui-review.localhost:15179',
    });
    vi.resetModules();

    const { dashboardSocketUrl } = await import('./index');
    expect(dashboardSocketUrl()).toBe('/dashboard');
  });

  it('VITE_API_BASE_URL 非空时拼接该基址（解释 compose build arg 烤入行为）', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:18000');
    vi.stubGlobal('location', {
      ...window.location,
      hostname: 'stp-ui-review.localhost',
      origin: 'http://stp-ui-review.localhost:15179',
    });
    vi.resetModules();

    const { dashboardSocketUrl } = await import('./index');
    expect(dashboardSocketUrl()).toBe('http://localhost:18000/dashboard');
  });
});
