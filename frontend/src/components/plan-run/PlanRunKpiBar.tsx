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

type Tone = 'gray' | 'orange' | 'red' | 'purple';

const VALUE_CLS: Record<Tone, string> = {
  gray: 'text-gray-800',
  orange: 'text-orange-600',
  red: 'text-red-600',
  purple: 'text-purple-600',
};

const DOT_CLS: Record<Tone, string> = {
  gray: 'bg-gray-300',
  orange: 'bg-orange-500',
  red: 'bg-red-500',
  purple: 'bg-purple-500',
};

function KpiStat({
  label,
  value,
  tone = 'gray',
  testId,
}: {
  label: string;
  value: number;
  tone?: Tone;
  testId?: string;
}) {
  return (
    <div className="flex items-center gap-1.5" data-testid={testId}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_CLS[tone]}`} />
      <span className={`font-mono text-sm font-bold tabular-nums ${VALUE_CLS[tone]}`}>
        {value}
      </span>
      <span className="text-[11px] uppercase tracking-wider text-gray-400">{label}</span>
    </div>
  );
}

function Divider() {
  return <span className="hidden h-4 w-px bg-gray-200 sm:block" />;
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
      className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border bg-white px-4 py-2.5 shadow-sm"
    >
      <KpiStat label="设备" value={total} testId="kpi-total" />
      <KpiStat label="运行" value={running} tone="orange" testId="kpi-running" />
      <KpiStat
        label="失败"
        value={failed}
        tone={failed > 0 ? 'red' : 'gray'}
        testId="kpi-failed"
      />
      <KpiStat
        label="失联"
        value={unknown}
        tone={unknown > 0 ? 'purple' : 'gray'}
        testId="kpi-unknown"
      />
      {hostCount > 0 && <KpiStat label="主机" value={hostCount} testId="kpi-hosts" />}

      {stageStr && (
        <>
          <Divider />
          <div className="flex items-center gap-1.5" data-testid="kpi-stage">
            <span className="h-1.5 w-1.5 rounded-full bg-orange-400" />
            <span className="text-sm font-semibold text-gray-700">{stageStr}</span>
            {patrolCycle != null && patrolCycle >= 0 && (
              <span className="font-mono text-xs text-gray-400">#{patrolCycle}</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
