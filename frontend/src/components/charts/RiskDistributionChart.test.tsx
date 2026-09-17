/**
 * #2494 / ADR-0045 D2·D3·D4：分布图的桶身份是**级别本身**，文案由徽标表出。
 *
 * 收敛前的形态：后端分布字段叫 `high/medium/low/unknown`、报告 DTO 出 `S/A/B`、
 * 结果页列表出 `HIGH/MEDIUM/LOW`、趋势又用第四态 `NONE` —— 同一概念四套词，
 * `status-badge` 查不到键就恒显「未知」（#2418 同型）。这里钉三条：
 *  1) 序列覆盖 S/A/B/UNKNOWN 四档，取值键与 `RiskDistribution` 字段一一配对；
 *  2) 文案取自 `status-badge` 的 RISK 表，饼图**不自带**第二份 S→高 映射（D3）；
 *  3) `unknown` 不并进入 `b`（D4：零事件 ≠ 低风险），也不在饼图里被省略。
 *
 * 断言为什么落在 `buildRiskSeries` 而不是 DOM：recharts 的图例在 jsdom 里不渲染
 * （零尺寸 + StableResponsiveContainer 的尺寸门禁），仓库既有的 chart 测试也因此只测
 * loading/empty——把映射抽成纯函数才测得到真判据，而不是假装有断言。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RiskDistributionChart } from './RiskDistributionChart';
import { buildRiskSeries, RISK_BUCKET_KEYS, RISK_LEVELS } from './riskBuckets';
import { resolveStatusEntry } from '@/components/ui/status-badge';
import { CHART_COLORS } from '@/design-system/colors';
import type { RiskDistribution } from '@/utils/api/types';

const allBuckets = (over: Partial<RiskDistribution> = {}): RiskDistribution => ({
  s: 0,
  a: 0,
  b: 0,
  unknown: 0,
  ...over,
});

describe('buildRiskSeries —— 桶身份 = 级别（D2）', () => {
  it('四档齐全、按严重度排序，取值键与后端 RiskDistribution 字段配对', () => {
    const series = buildRiskSeries(allBuckets({ s: 1, a: 1, b: 1, unknown: 1 }));
    expect(series.map((p) => p.level)).toEqual([...RISK_LEVELS]);
    expect(RISK_LEVELS).toEqual(['S', 'A', 'B', 'UNKNOWN']);
    // 分布字段名 = 级别小写（D2「桶名就是级别本身」），不是 high/medium/low
    expect(RISK_BUCKET_KEYS).toEqual(['s', 'a', 'b', 'unknown']);
    expect([...Object.keys(allBuckets())].sort()).toEqual([...RISK_BUCKET_KEYS].sort());
  });

  it('零计数桶不占图例（没有依据可画，不该画出一个 0 的扇区）', () => {
    const series = buildRiskSeries(allBuckets({ s: 2, b: 1, unknown: 3 }));
    expect(series.map((p) => p.level)).toEqual(['S', 'B', 'UNKNOWN']);
  });

  it('全零（新环境/新窗口）映射为空数组而不是崩', () => {
    expect(buildRiskSeries(allBuckets())).toEqual([]);
  });
});

describe('buildRiskSeries —— 文案只有一处翻译（D3）', () => {
  it.each(RISK_LEVELS)('%s 的图例文案就是徽标表里那条，不是饼图自带的一份', (level) => {
    const series = buildRiskSeries(allBuckets({ [level.toLowerCase()]: 1 } as Partial<RiskDistribution>));
    expect(series).toHaveLength(1);
    expect(series[0].name).toBe(resolveStatusEntry('risk', level).label);
  });

  it('四档文案与着色：高/中/低/未知，色阶按级别区分（S 与 B 不同色）', () => {
    const series = buildRiskSeries(allBuckets({ s: 1, a: 1, b: 1, unknown: 1 }));
    expect(series.map((p) => [p.level, p.name, p.color])).toEqual([
      ['S', '高', CHART_COLORS.error],
      ['A', '中', CHART_COLORS.warning],
      ['B', '低', CHART_COLORS.success],
      ['UNKNOWN', '未知', CHART_COLORS.muted],
    ]);
    expect(new Set(series.map((p) => p.color)).size).toBe(4);
  });
});

describe('buildRiskSeries —— UNKNOWN 不可折叠（D4）', () => {
  it('零事件不并进 b：unknown:5 只出 UNKNOWN 一档，b 保持缺席', () => {
    const series = buildRiskSeries(allBuckets({ unknown: 5 }));
    expect(series).toHaveLength(1);
    expect(series[0]).toMatchObject({ level: 'UNKNOWN', name: '未知', value: 5 });
    expect(series.find((p) => p.level === 'B')).toBeUndefined();
  });

  it('有事件但非 S/A 才算 b，与 unknown 同屏各占一档', () => {
    const series = buildRiskSeries(allBuckets({ b: 2, unknown: 5 }));
    expect(series.map((p) => [p.level, p.value])).toEqual([
      ['B', 2],
      ['UNKNOWN', 5],
    ]);
  });
});

describe('RiskDistributionChart 渲染', () => {
  it('加载中给骨架，不落空卡片', () => {
    const { container } = render(<RiskDistributionChart data={allBuckets()} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('全零时显式说明「暂无风险数据」，不留空白面板', () => {
    render(<RiskDistributionChart data={allBuckets()} />);
    expect(screen.getByText('风险分布')).toBeInTheDocument();
    expect(screen.getByText('暂无风险数据')).toBeInTheDocument();
  });

  it('有桶时出标题，不因 recharts 在 jsdom 不渲染而报错', () => {
    render(<RiskDistributionChart data={allBuckets({ s: 1, unknown: 2 })} />);
    expect(screen.getByText('风险分布')).toBeInTheDocument();
    expect(screen.queryByText('暂无风险数据')).not.toBeInTheDocument();
  });
});
