import { AlertTriangle } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';
import type { HostFailureRateItem } from '@/utils/api/types';
import { VerticalRateBarChart, type RateBarRow } from './VerticalRateBarChart';

/** @deprecated use `HostFailureRateItem` from `@/utils/api/types` */
export type HostFailureRateData = HostFailureRateItem;

interface HostFailureRateChartProps {
  data?: HostFailureRateItem[];
  isLoading?: boolean;
}

function getLabel(d: HostFailureRateItem): string {
  return d.hostname
    ? d.hostname.length > 16
      ? d.hostname.slice(0, 15) + '...'
      : d.hostname
    : d.host_id.slice(0, 12);
}

function getBarColor(rate: number): string {
  if (rate >= 0.3) return CHART_COLORS.error;
  if (rate >= 0.1) return CHART_COLORS.warning;
  return CHART_COLORS.primary;
}

function renderTooltip(item: RateBarRow<HostFailureRateItem>) {
  return (
    <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
      <div className="text-muted-foreground mb-1">{item.hostname || item.host_id}</div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">失败率:</span>
        <span className="font-medium">{item.ratePct}%</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">失败/总计:</span>
        <span className="font-medium">{item.failed}/{item.total_jobs}</span>
      </div>
    </div>
  );
}

export function HostFailureRateChart({ data, isLoading }: HostFailureRateChartProps) {
  return (
    <VerticalRateBarChart
      title="节点失败率排行 (30d)"
      icon={<AlertTriangle size={16} className="text-muted-foreground" />}
      data={data}
      isLoading={isLoading}
      getLabel={getLabel}
      getRate={(d) => d.failure_rate}
      getBarColor={getBarColor}
      renderTooltip={renderTooltip}
    />
  );
}
