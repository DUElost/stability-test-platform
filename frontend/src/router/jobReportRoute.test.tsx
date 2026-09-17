/**
 * #2420：报告页路由的权威形状与"旧路径只重定向"的接线契约。
 *
 * 为什么断言源码而不是渲染整棵树：本单要钉的正是"两条路径里只有 `jobs/:jobId` 会
 * 渲染页面"——渲染只能证明一条能跑，证明不了另一条**没有**变成第二权威（同法先例
 * 见 `routeTitleWiring.test.tsx` / `tests/test_ci_promtool_scenario_gate.py`）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(resolve(__dirname, 'index.tsx'), 'utf-8');

describe('Job 报告路由 (#2420)', () => {
  it('权威形状 /jobs/:jobId/report 渲染 RunReportPage，且只登记一次', () => {
    expect(
      source.match(/<Route path="jobs\/:jobId\/report" element=\{<RunReportPage \/>\}/g)?.length,
    ).toBe(1);
  });

  it('旧形状 /runs/:runId/report 只作重定向，不再是第二条渲染入口', () => {
    const legacy = source.match(/<Route path="runs\/:runId\/report" element=\{([^}]+)\}/);
    expect(legacy, '旧路径必须仍在（历史深链不能 404）').not.toBeNull();
    expect(legacy?.[1]).toContain('LegacyJobReportRedirect');
    expect(legacy?.[1]).not.toContain('RunReportPage');
  });

  it('重定向时带上查询串（?planRun= 的归属校验不能因换路径而丢）', () => {
    expect(source).toMatch(/\$\{search\}/);
  });
});
