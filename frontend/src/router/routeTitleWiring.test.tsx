/**
 * #2363 接线契约：路由级标题的登记点必须真的挂在路由器里。
 *
 * 为什么用源码断言而不是渲染整棵 AppRouter：标题的失效形态正是「没人登记」，
 * 而渲染整棵树要先过 AuthGate + lazy Suspense，测到的其实是登录重定向。
 * 同法先例：`tests/test_ci_promtool_scenario_gate.py`（CI 接线）、
 * `tests/test_lock_order_pr_path_contract.py`（守卫接线）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROUTER = resolve(__dirname, 'index.tsx');

describe('RouteTitle 接线 (#2363)', () => {
  const source = readFileSync(ROUTER, 'utf-8');

  it('路由器里登记且只登记一次 <RouteTitle/>', () => {
    expect(source.match(/<RouteTitle\s*\/>/g)?.length).toBe(1);
  });

  it('挂载点在 <BrowserRouter> 之内、<Routes> 之前（否则 useLocation 取不到路径）', () => {
    const BrowserRouterAt = source.indexOf('<BrowserRouter>');
    const mountAt = source.indexOf('<RouteTitle />');
    const routesAt = source.indexOf('<Routes>');
    expect(BrowserRouterAt).toBeGreaterThan(-1);
    expect(mountAt).toBeGreaterThan(BrowserRouterAt);
    expect(mountAt).toBeLessThan(routesAt);
  });

  it('页面不得再各自登记静态标题（同一事实只留路由表一处）', () => {
    const staticClaims = [
      '../pages/projects/ProjectsPage.tsx',
      '../pages/orchestration/PlanListPage.tsx',
      '../pages/results/ResultsPage.tsx',
      '../pages/execution/PlanRunListPage.tsx',
      '../pages/suites/TestSuitesPage.tsx',
    ].filter((rel) => readFileSync(resolve(__dirname, rel), 'utf-8').includes('useDocumentTitle('));
    expect(staticClaims, `这些页面的静态标题应改由路由表提供：${staticClaims.join(', ')}`).toEqual([]);
  });
});
