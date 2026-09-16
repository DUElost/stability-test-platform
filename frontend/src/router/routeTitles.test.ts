/**
 * #2363 —— 路由级标题表：缺口路由有标题、名字与侧栏同源、深路径赢过前缀。
 */
import { describe, expect, it } from 'vitest';

import { navGroups } from '@/layouts/navItems';
import { resolveRouteTitle } from './routeTitles';

describe('resolveRouteTitle (#2363)', () => {
  it('issue 点名的三条缺口路由都有标题', () => {
    expect(resolveRouteTitle('/execution/plan-execute')).toBe('执行 Plan');
    expect(resolveRouteTitle('/issue-tracker')).toBe('问题追踪');
    expect(resolveRouteTitle('/hosts')).toBe('主机集群');
  });

  it('侧栏每一条导航路径都能解析出同名标题（新增路由不会再漏）', () => {
    const items = navGroups.flatMap((group) => group.items);
    expect(items.length).toBeGreaterThan(5);
    for (const item of items) {
      expect(resolveRouteTitle(item.path), `路由 ${item.path} 无标题`).toBe(item.label);
    }
  });

  it('更深的路由赢过前缀（/settings/ai-assistant 不被 /settings 抢走）', () => {
    expect(resolveRouteTitle('/settings/ai-assistant')).toBe('AI 助手设置');
    expect(resolveRouteTitle('/settings')).toBe('系统设置');
    expect(resolveRouteTitle('/assistant/approvals')).toBe('AI 助手审批');
    expect(resolveRouteTitle('/assistant')).toBe('AI 助手');
    expect(resolveRouteTitle('/')).toBe('仪表盘');
  });

  it('参数段匹配详情页；未登记路径返回 null（留给页面 claim）', () => {
    expect(resolveRouteTitle('/execution/plan-runs/409/logs')).toBe('Run 日志');
    expect(resolveRouteTitle('/execution/plan-runs/409')).toBe('Plan Run 详情');
    expect(resolveRouteTitle('/projects/STP')).toBe('项目详情');
    expect(resolveRouteTitle('/runs/abc/report')).toBe('Run 报告');
    expect(resolveRouteTitle('/totally-unknown/deep/x')).toBeNull();
  });
});
