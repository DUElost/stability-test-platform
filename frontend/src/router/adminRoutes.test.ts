/**
 * #2360（裁决 A）：admin-only 面必须**入口隐藏 + 路由门控**成对出现。
 *
 * 现状是「可见但必 403」：`/wifi` 的列表/详情/loads 全是 `require_admin`，而侧边栏
 * 对非 admin 照常展示入口、路由也没走 `AdminRoute`——点进去只会得到满页 403。
 * 只改一侧会留下另一半：只藏入口，直连 URL 仍是满页 403；只门控路由，入口仍误导。
 *
 * 判据是结构性的（源码 + 导航数据），不依赖渲染：这一类错配正是「两处各改一半」造成的。
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { navGroups } from '@/layouts/navItems';

function routerSource(): string {
  const candidates = [
    path.resolve(process.cwd(), 'src/router/index.tsx'),
    path.resolve(process.cwd(), 'frontend/src/router/index.tsx'),
  ];
  for (const candidate of candidates) {
    try {
      return readFileSync(candidate, 'utf-8');
    } catch {
      continue;
    }
  }
  throw new Error('找不到 src/router/index.tsx（cwd 不在预期位置？）');
}

/** `<Route element={<AdminRoute />}>` 块的文本（从该行到它自己的 `</Route>`）。 */
function adminRouteBlock(src: string): string {
  const start = src.indexOf('<Route element={<AdminRoute />}>');
  expect(start, 'AdminRoute 块不存在（改名？）').toBeGreaterThanOrEqual(0);
  const end = src.indexOf('</Route>', start);
  expect(end, 'AdminRoute 块未闭合').toBeGreaterThan(start);
  return src.slice(start, end);
}

describe('admin-only 面：入口隐藏 + 路由门控（#2360）', () => {
  it('/wifi 与 /storage 都在 AdminRoute 块内', () => {
    const block = adminRouteBlock(routerSource());
    expect(block).toContain('path="wifi"');
    expect(block).toContain('path="storage"');
  });

  it('/wifi 与 /storage 的导航项都标了 adminOnly', () => {
    const items = navGroups.flatMap((group) => group.items);
    const wifi = items.find((item) => item.path === '/wifi');
    const storage = items.find((item) => item.path === '/storage');
    expect(wifi?.adminOnly, 'WiFi 资源池入口未按角色隐藏（#2360 裁决 A）').toBe(true);
    expect(storage?.adminOnly, '文件服务器入口未按角色隐藏').toBe(true);
  });

  it('AdminRoute 块外的路由不得出现 wifi（防止又挪回去）', () => {
    const src = routerSource();
    const block = adminRouteBlock(src);
    const outside = src.replace(block, '');
    expect(outside).not.toContain('path="wifi"');
  });
});
