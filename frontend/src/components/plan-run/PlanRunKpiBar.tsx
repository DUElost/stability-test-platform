import { KPI_BAR_DOT, KPI_TONE, PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

interface Props {
  devices?: PlanRunDevicesPayload;
  currentStage?: string | null;
  patrolCycle?: number | null;
}

const STAGE_LABEL: Record<string, string> = {
  init: 'INIT',
  patrol: 'PATROL',
  teardown: 'TEARDOWN',
  done: 'DONE',
  pending: 'PENDING',
};

type Tone = 'default' | 'warning' | 'destructive' | 'info';

const VALUE_CLS: Record<Tone, string> = {
  default: KPI_TONE.default.value,
  warning: KPI_TONE.warning.value,
  destructive: KPI_TONE.destructive.value,
  info: KPI_TONE.info.value,
};

const DOT_CLS: Record<Tone, string> = {
  default: KPI_BAR_DOT.default,
  warning: KPI_BAR_DOT.warning,
  destructive: KPI_BAR_DOT.destructive,
  info: KPI_BAR_DOT.info,
};

function KpiStat({
  label,
  value,
  tone = 'default',
  testId,
}: {
  label: string;
  value: number;
  tone?: Tone;
  testId?: string;
}) {
  return (
    <div className="flex items-center gap-1.5" data-testid={testId}>
      <span className={cn('h-1.5 w-1.5 rounded-full', DOT_CLS[tone])} />
      <span className={cn('font-mono text-sm font-bold tabular-nums', VALUE_CLS[tone])}>
        {value}
      </span>
      <span className={cn('text-[11px] uppercase tracking-wider', TEXT.subtitle)}>{label}</span>
    </div>
  );
}

function Divider() {
  return <span className="hidden h-4 w-px bg-border sm:block" />;
}

export default function PlanRunKpiBar({
  devices,
  currentStage,
  patrolCycle,
}: Props) {
  const total = devices?.total ?? 0;
  const byStatus = devices?.by_status ?? {};
  const running = byStatus.running ?? 0;
  const failed = byStatus.failed ?? 0;
  const unknown = byStatus.unknown ?? 0;
  const hostCount = Object.keys(devices?.by_host ?? {}).length;

  const stageStr = currentStage
    ? STAGE_LABEL[currentStage] ?? currentStage.toUpperCase()
    : null;

  return (
    <div
      data-testid="plan-run-kpi-bar"
      className={cn('flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5', PANEL.root)}
    >
      <KpiStat label="设备" value={total} testId="kpi-total" />
      <KpiStat label="运行" value={running} tone="warning" testId="kpi-running" />
      <KpiStat
        label="失败"
        value={failed}
        tone={failed > 0 ? 'destructive' : 'default'}
        testId="kpi-failed"
      />
      <KpiStat
        label="已断开"
        value={unknown}
        tone={unknown > 0 ? 'info' : 'default'}
        testId="kpi-unknown"
      />
      {hostCount > 0 && <KpiStat label="主机" value={hostCount} testId="kpi-hosts" />}

      {stageStr && (
        <>
          <Divider />
          <div className="flex items-center gap-1.5" data-testid="kpi-stage">
            <span className="h-1.5 w-1.5 rounded-full bg-warning/70" />
            <span className={cn('text-sm font-semibold', TEXT.body)}>{stageStr}</span>
            {patrolCycle != null && patrolCycle >= 0 && (
              <span className={cn('font-mono text-xs', TEXT.subtitle)}>#{patrolCycle}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
