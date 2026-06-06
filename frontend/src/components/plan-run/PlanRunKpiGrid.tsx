import type { PlanRunDevicesPayload } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  devices?: PlanRunDevicesPayload;
  currentStage?: string | null;
  patrolCycle?: number | null;
}

type Tone = 'orange' | 'red' | 'purple' | 'amber' | 'default';

const TONE_CLS: Record<Tone, { value: string; label: string }> = {
  orange:  { value: 'text-orange-600 font-bold', label: 'text-orange-500' },
  red:     { value: 'text-red-600   font-bold', label: 'text-red-500' },
  purple:  { value: 'text-purple-600 font-bold', label: 'text-purple-500' },
  amber:   { value: 'text-amber-600  font-bold', label: 'text-amber-500' },
  default: { value: 'text-gray-800  font-bold', label: 'text-gray-500' },
};

function Cell({
  value,
  label,
  tone = 'default',
  testId,
}: {
  value: number | string;
  label: string;
  tone?: Tone;
  testId: string;
}) {
  const cls = TONE_CLS[tone];
  return (
    <div
      className="flex flex-col items-center justify-center rounded-lg border bg-white py-2.5 px-1 shadow-sm"
      data-testid={testId}
    >
      <span className={`text-2xl leading-none ${cls.value}`}>{value}</span>
      <span className={`mt-1 text-[11px] ${cls.label}`}>{label}</span>
    </div>
  );
}

export default function PlanRunKpiGrid({ devices, currentStage, patrolCycle }: Props) {
  const byStatus = devices?.by_status ?? {};
  const total      = devices?.total                ?? 0;
  const running    = byStatus.running              ?? 0;
  const completed  = byStatus.completed            ?? 0;
  const failed     = byStatus.failed               ?? 0;
  const risk       = byStatus.risk                 ?? 0;
  const backoff    = byStatus.backoff              ?? 0;

  const stageLabel =
    currentStage === 'init'     ? '初始化' :
    currentStage === 'patrol'   ? '巡检'   :
    currentStage === 'teardown' ? '清理'   : '—';

  return (
    <div className="space-y-2.5">
      <SectionHeader title="关键指标" />
      <div className="grid grid-cols-2 gap-2">
        <Cell value={total}     label="设备总数"   testId="kpi-total"     />
        <Cell value={stageLabel} label={patrolCycle != null ? `周期 #${patrolCycle}` : '当前阶段'} testId="kpi-stage" />
        <Cell value={running}   label="运行中"     tone="orange" testId="kpi-running"   />
        <Cell value={completed} label="已完成"     testId="kpi-completed" />
        <Cell value={failed}    label="失败"       tone={failed > 0 ? 'red' : 'default'}     testId="kpi-failed"    />
        <Cell value={risk + backoff} label="风险/退避" tone={risk + backoff > 0 ? 'amber' : 'default'} testId="kpi-risk-backoff" />
      </div>
    </div>
  );
}
