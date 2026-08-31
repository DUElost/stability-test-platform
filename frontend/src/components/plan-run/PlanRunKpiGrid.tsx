import type { PlanRunDevicesPayload } from '@/utils/api/types';
import { BORDER, KPI_TONE, STAT, SURFACE, type KpiTone } from '@/design-system/tokens';
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
        'flex flex-col items-center justify-center rounded-lg border py-2.5 px-1 shadow-none',
        SURFACE.elevated,
        BORDER.default,
      )}
      data-testid={testId}
    >
      <span className={cn(STAT.value, 'text-2xl', cls.value)}>{value}</span>
      <span className={cn('mt-1', STAT.label, cls.label)}>{label}</span>
    </div>
  );
}

export default function PlanRunKpiGrid({ devices, currentStage, patrolCycle }: Props) {
  const byStatus = devices?.by_status ?? {};
  const byLinkStatus = devices?.by_link_status;
  const total = devices?.total ?? 0;
  const running = byStatus.running ?? 0;
  const completed = byStatus.completed ?? 0;
  const failed = byStatus.failed ?? 0;
  const unknown = byStatus.unknown ?? 0;
  const backoff = byStatus.backoff ?? 0;
  // 执行维度:Job 失联(lease/心跳超时)+ 退避。by_status 现在只投影 Job 状态,
  // 不再掺设备连接 —— 设备断连要看下面的 linkAbnormal。
  const disconnectedAndBackoff = unknown + backoff;
  const disconnectedTone: KpiTone =
    unknown > 0 ? 'info' : disconnectedAndBackoff > 0 ? 'warning' : 'default';

  // 连接维度:非 online 的一律算异常。用「总数 - online」而非枚举各异常态,
  // 这样后端将来新增 link 状态时前端不会漏计。
  // 老后端不返回 by_link_status:那时 by_status 还是基于 ui_status,
  // 其 unknown 恰好表示断连,拿它兜底。
  const linkAbnormal = byLinkStatus
    ? Math.max(0, (byLinkStatus.all ?? total) - (byLinkStatus.online ?? 0))
    : unknown;
  const linkTone: KpiTone = linkAbnormal > 0 ? 'warning' : 'default';

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
        <Cell value={running} label="运行中" tone="warning" testId="kpi-running" />
        <Cell value={completed} label="已完成" tone="success" testId="kpi-completed" />
        <Cell
          value={failed}
          label="失败"
          tone={failed > 0 ? 'destructive' : 'default'}
          testId="kpi-failed"
        />
        <Cell
          value={disconnectedAndBackoff}
          label="Job 失联/退避"
          tone={disconnectedTone}
          testId="kpi-disconnected-backoff"
        />
        {/* 连接维度与上面的执行维度正交,同一台设备可能同时计入两处,
            所以横跨整行单独呈现,避免读成「又一个执行状态」。 */}
        <div className="col-span-2">
          <Cell
            value={linkAbnormal}
            label="设备连接异常"
            tone={linkTone}
            testId="kpi-link-abnormal"
          />
        </div>
      </div>
    </div>
  );
}
