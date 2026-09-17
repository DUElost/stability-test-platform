/**
 * 风险分布图的桶序列（ADR-0045 D2·D3·D4，#2494）。
 *
 * 为什么单独成文件（而不是留在 RiskDistributionChart.tsx 里）：
 *  1) recharts 的图例在 jsdom（零尺寸 + StableResponsiveContainer 的尺寸门禁）下**不渲染**，
 *     「桶名漂移」这类纯数据缺陷在 DOM 上测不到——仓库既有的 chart 测试
 *     （PlanSuccessRateChart / HostFailureRateChart）也因此只测 loading/empty；
 *     抽成纯函数后判据能真的跑，而不是假装有断言。
 *  2) eslint `react-refresh/only-export-components`：组件文件只导出组件。
 *
 * 词表（D2·D3）：序列的**身份是级别本身** `S/A/B/UNKNOWN`；「高/中/低」**不在这里定义**，
 * 而是查 `status-badge` 的 RISK 表（`resolveStatusEntry`）——饼图自带一份 S→高 就是
 * D3 要消除的「第二处翻译」，也是 #2494 这一族缺陷的根因。
 * 只有着色留在这里：变体名（destructive/warning/…）到图表色板是渲染关注点，不是词表。
 *
 * D4：`UNKNOWN`（零判定依据）与 `B`（有事件但非 S/A）是两个桶，且 UNKNOWN 在饼图里
 * 不得被省略——「没有采到异常」≠「查过且没风险」。
 */
import { resolveStatusEntry } from '@/components/ui/status-badge';
import { CHART_COLORS } from '@/design-system/colors';
import type { RiskDistribution, RiskLevel } from '@/utils/api/types';

/** JSON 字段名 = 级别小写（D2「桶名就是级别本身，小写只是字段风格」）。 */
export const RISK_BUCKET_KEYS = ['s', 'a', 'b', 'unknown'] as const;
export type RiskBucketKey = (typeof RISK_BUCKET_KEYS)[number];

/** 级别 → 该级别的分布字段；写死配对，字段改名时 TS 立刻红（不用 level.toLowerCase()）。 */
const BUCKET_BY_LEVEL: Record<RiskLevel, RiskBucketKey> = {
  S: 's',
  A: 'a',
  B: 'b',
  UNKNOWN: 'unknown',
};

/** 展示顺序即严重度顺序；饼图从 S 起、零事件垫底。 */
export const RISK_LEVELS: readonly RiskLevel[] = ['S', 'A', 'B', 'UNKNOWN'];

const COLORS: Record<RiskLevel, string> = {
  S: CHART_COLORS.error,
  A: CHART_COLORS.warning,
  B: CHART_COLORS.success,
  UNKNOWN: CHART_COLORS.muted,
};

export interface RiskSeriesPoint {
  /** 级别（对外词表本身）——测试与 Cell key 都用它，不靠位置序号。 */
  level: RiskLevel;
  /** 徽标表出的文案（唯一翻译点），不是本文件的第二套映射。 */
  name: string;
  value: number;
  color: string;
}

/** 一行 = 一个风险级别桶；零计数桶不占图例（没有依据可画，不该画出一个 0 的扇区）。 */
export function buildRiskSeries(data: RiskDistribution): RiskSeriesPoint[] {
  return RISK_LEVELS.map((level) => ({
    level,
    name: resolveStatusEntry('risk', level).label,
    value: data[BUCKET_BY_LEVEL[level]] ?? 0,
    color: COLORS[level],
  })).filter((item) => item.value > 0);
}
