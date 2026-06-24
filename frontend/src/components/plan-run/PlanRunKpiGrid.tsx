import type { PlanRunDevicesPayload } from '@/utils/api/types';
import { BORDER, KPI_TONE, SURFACE, type KpiTone } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import SectionHeader from './SectionHeader';

interface Props {
  devices?: PlanRunDevicesPayload;
  currentStage?: string | null;
  patrolCycle?: number | null;
}

function Cell({
  value,
  label,
  tone = 'default',
  testId,
}: {
  value: number | string;
  label: string;
  tone?: KpiTone;
  testId: string;
}) {
  const cls = KPI_TONE[tone];
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border py-2.5 px-1 shadow-sm',
        SURFACE.elevated,
        BORDER.default,
      )}
      data-testid={testId}
    >
      <span className={cn('text-2xl leading-none', cls.value)}>{value}</span>
      <span className={cn('mt-1 text-[11px]', cls.label)}>{label}</span>
    </div>
  );
}

export default function PlanRunKpiGrid({ devices, currentStage, patrolCycle }: Props) {
  const byStatus = devices?.by_status ?? {};
  const total = devices?.total ?? 0;
  const running = byStatus.running ?? 0;
  const completed = byStatus.completed ?? 0;
  const failed = byStatus.failed ?? 0;
  const unknown = byStatus.unknown ?? 0;
  const backoff = byStatus.backoff ?? 0;
  const disconnectedAndBackoff = unknown + backoff;
  const disconnectedTone: KpiTone =
    unknown > 0 ? 'info' : disconnectedAndBackoff > 0 ? 'warning' : 'default';

  const stageLabel =
    currentStage === 'init'
      ? '初始化'
      : currentStage === 'patrol'
        ? '巡检'
        : currentStage === 'teardown'
          ? '清理'
          : '—';

  return (
    <div className="space-y-2.5">
      <SectionHeader title="关键指标" />
      <div className="grid grid-cols-2 gap-2">
        <Cell value={total} label="设备总数" testId="kpi-total" />
        <Cell
          value={stageLabel}
          label={patrolCycle != null ? `周期 #${patrolCycle}` : '当前阶段'}
          testId="kpi-stage"
        />
        <Cell value={running} label="运行中" tone="primary" testId="kpi-running" />
        <Cell value={completed} label="已完成" tone="success" testId="kpi-completed" />
        <Cell
          value={failed}
          label="失败"
          tone={failed > 0 ? 'destructive' : 'default'}
          testId="kpi-failed"
        />
        <Cell
          value={disconnectedAndBackoff}
          label="已断开/退避"
          tone={disconnectedTone}
          testId="kpi-disconnected-backoff"
        />
      </div>
    </div>
  );
}
