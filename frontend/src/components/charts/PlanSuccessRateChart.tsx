import { CheckCircle } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';
import type { PlanSuccessRateItem } from '@/utils/api/types';
import { VerticalRateBarChart, type RateBarRow } from './VerticalRateBarChart';

/** @deprecated use `PlanSuccessRateItem` from `@/utils/api/types` */
export type PlanSuccessRateData = PlanSuccessRateItem;

interface PlanSuccessRateChartProps {
  data?: PlanSuccessRateItem[];
  isLoading?: boolean;
}

function getLabel(d: PlanSuccessRateItem): string {
  return d.plan_name.length > 20 ? d.plan_name.slice(0, 19) + '...' : d.plan_name;
}

function getBarColor(rate: number): string {
  if (rate >= 0.95) return CHART_COLORS.success;
  if (rate >= 0.8) return CHART_COLORS.warning;
  return CHART_COLORS.error;
}

function renderTooltip(item: RateBarRow<PlanSuccessRateItem>) {
  return (
    <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
      <div className="text-muted-foreground mb-1">{item.plan_name}</div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">成功率:</span>
        <span className="font-medium">{item.ratePct}%</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">通过/总计:</span>
        <span className="font-medium">{item.passed}/{item.total_jobs}</span>
      </div>
    </div>
  );
}

export function PlanSuccessRateChart({ data, isLoading }: PlanSuccessRateChartProps) {
  return (
    <VerticalRateBarChart
      title="方案成功率"
      icon={<CheckCircle size={16} className="text-muted-foreground" />}
      data={data}
      isLoading={isLoading}
      yAxisWidth={150}
      getLabel={getLabel}
      getRate={(d) => d.pass_rate}
      getBarColor={getBarColor}
      renderTooltip={renderTooltip}
    />
  );
}
