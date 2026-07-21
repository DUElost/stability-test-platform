import { CheckCircle2, Clock3, Info, Trash2 } from 'lucide-react';
import type { PlanRun, PlanRunPreview } from '@/utils/api';
import type { CapacityPlanRow, ReadinessDevice } from '@/utils/planExecuteReadiness';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDurationSeconds } from '@/utils/format';
import { DuplicateLaunchBanner } from './DuplicateLaunchBanner';
import type { DuplicateMatch } from './planExecuteDuplicate';
import { RecentPlanRunsInline } from './RecentPlanRunsInline';
import type { WallClockEstimate } from './planExecuteWallClock';

interface DispatchCockpitProps {
  planName: string;
  executableStepCount: number;
  devices: ReadinessDevice[];
  capacityRows: CapacityPlanRow[];
  readyCount: number;
  blockedCount: number;
  warnings: string[];
  selectedHostActiveJobs: number;
  patrolIntervalSeconds?: number | null;
  timeoutSeconds?: number | null;
  failureThreshold?: number | null;
  note: string;
  preview: PlanRunPreview | null;
  wallClock: WallClockEstimate;
  recentRuns: PlanRun[];
  recentRunsLoading: boolean;
  duplicateMatch: DuplicateMatch | null;
  onNoteChange: (value: string) => void;
  onEditPlan: () => void;
  onOpenRun: (runId: number) => void;
  onRemoveBlocked: () => void;
}

function formatFailureThreshold(threshold: number | null | undefined): string {
  if (threshold == null) return '未设置（按默认 5% 生效）';
  return `${Math.round(threshold * 100)}%`;
}

function ParameterInfo({ label, tip }: { label: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" aria-label={`${label}说明`} className="text-muted-foreground hover:text-foreground">
            <Info className="h-3.5 w-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{tip}</TooltipContent>
      </Tooltip>
    </span>
  );
}

export function DispatchCockpit({
  planName,
  executableStepCount,
  devices,
  capacityRows,
  readyCount,
  blockedCount,
  warnings,
  selectedHostActiveJobs,
  patrolIntervalSeconds,
  timeoutSeconds,
  failureThreshold,
  note,
  preview,
  wallClock,
  recentRuns,
  recentRunsLoading,
  duplicateMatch,
  onNoteChange,
  onEditPlan,
  onOpenRun,
  onRemoveBlocked,
}: DispatchCockpitProps) {
  const immediateCount = capacityRows.reduce((total, row) => total + (row.immediate ?? 0), 0);
  const queuedCount = capacityRows.reduce((total, row) => total + (row.queued ?? 0), 0);
  const unknownCapacityCount = capacityRows.filter((row) => row.effectiveSlots == null).length;

  return (
    <TooltipProvider>
      <div className="space-y-4" data-testid="dispatch-cockpit">
        {duplicateMatch ? (
          <DuplicateLaunchBanner match={duplicateMatch} onOpenRun={onOpenRun} />
        ) : null}

        {preview ? (
          <div className="flex items-center gap-2 rounded-lg border border-success/40 bg-success/10 px-3 py-2 text-sm text-success">
            <CheckCircle2 className="h-4 w-4" />
            预览已生成并冻结 {preview.device_ids.length || preview.device_count || devices.length} 台设备；请再次确认发起
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-xl border bg-card p-4">
            <div className={cn('text-xs', TEXT.subtitle)}>本次设备</div>
            <div className="mt-1 text-2xl font-semibold">{devices.length}</div>
            <div className={cn('mt-1 text-xs', TEXT.subtitle)}>跨 {capacityRows.length} 节点</div>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <div className={cn('text-xs', TEXT.subtitle)}>可立即消化</div>
            <div className="mt-1 text-2xl font-semibold">{unknownCapacityCount ? '—' : immediateCount}</div>
            <div className={cn('mt-1 text-xs', TEXT.subtitle)}>按心跳剩余槽位合计</div>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <div className={cn('text-xs', TEXT.subtitle)}>将排队</div>
            <div className={cn('mt-1 text-2xl font-semibold', queuedCount > 0 && 'text-warning')}>
              {unknownCapacityCount ? '—' : queuedCount}
            </div>
            <div className={cn('mt-1 text-xs', TEXT.subtitle)}>
              {unknownCapacityCount ? `${unknownCapacityCount} 个节点缺槽位数据` : '节点侧容量提示'}
            </div>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <div className={cn('flex items-center gap-1 text-xs', TEXT.subtitle)}>
              <Clock3 className="h-3.5 w-3.5" />历史墙钟参考
            </div>
            <div className="mt-1 text-xl font-semibold">
              {wallClock.averageSeconds == null
                ? '暂无'
                : `~${formatDurationSeconds(wallClock.averageSeconds, 'compact', '暂无')}`}
            </div>
            <div className={cn('mt-1 text-xs', TEXT.subtitle)}>
              {wallClock.averageSeconds == null
                ? '至少需要 2 次有效终态样本'
                : `近 ${wallClock.sampleCount} 次整次均值 · 长稳可能为天级`}
            </div>
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">按节点派发计划</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/60 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">节点</th>
                      <th className="px-3 py-2 font-medium">本次选中</th>
                      <th className="px-3 py-2 font-medium">剩余槽位</th>
                      <th className="px-3 py-2 font-medium">容量预估</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {capacityRows.map((row) => (
                      <tr key={row.hostId}>
                        <td className="px-3 py-3">
                          <div className="font-medium">{row.hostLabel}</div>
                          <div className={cn('mt-0.5 text-[11px]', TEXT.subtitle)}>
                            {row.healthStatus || '健康状态未知'}
                            {row.healthReasons.length ? ` · ${row.healthReasons.join('、')}` : ''}
                          </div>
                        </td>
                        <td className="px-3 py-3">{row.selected}</td>
                        <td className="px-3 py-3">{row.effectiveSlots ?? '暂无'}</td>
                        <td className={cn('px-3 py-3 font-medium', (row.queued ?? 0) > 0 ? 'text-warning' : 'text-success')}>
                          {row.effectiveSlots == null
                            ? <span className={TEXT.subtitle}>槽位数据缺失，不估算</span>
                            : row.queued
                              ? `${row.immediate} 立即 · ${row.queued} 将排队`
                              : '全部立即执行'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className={cn('rounded-lg bg-muted px-3 py-2 text-xs leading-5', TEXT.subtitle)}>
                “将排队”仅表示超过节点当前 effective_slots 的选中量，不是 PlanRun 级 QUEUED 准入，也不代表精确开跑时间。
              </div>
              <details className="rounded-lg border">
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">查看设备 Serial（{devices.length}）</summary>
                <div className="grid max-h-40 gap-x-4 overflow-auto border-t p-3 font-mono text-xs leading-6 sm:grid-cols-2">
                  {devices.map((device) => <div key={device.id}>{device.serial}</div>)}
                </div>
              </details>
            </CardContent>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardContent className="pt-5">
                <RecentPlanRunsInline
                  runs={recentRuns}
                  loading={recentRunsLoading}
                  onOpenRun={onOpenRun}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-base">参数与检查单</CardTitle>
                  <Button type="button" variant="outline" size="sm" onClick={onEditPlan}>编辑 Plan</Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <ParameterInfo label="巡检周期" tip="到达周期后触发下一轮巡检脚本；过短可能持续占用 Agent 执行槽位。" />
                  <strong>{formatDurationSeconds(patrolIntervalSeconds, 'precise', '未设置')}</strong>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <ParameterInfo label="超时" tip="整个 PlanRun 超时后中止；已完成步骤的结果会保留。" />
                  <strong>{formatDurationSeconds(timeoutSeconds, 'precise', '未设置')}</strong>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <ParameterInfo label="失败阈值" tip="失败 Job 占比超过阈值时标记 PlanRun 失败；未设置时按默认 5% 生效。" />
                  <strong>{formatFailureThreshold(failureThreshold)}</strong>
                </div>
                <div className="border-t pt-3">
                  <div className="font-medium">{planName}</div>
                  <div className={cn('mt-1 text-xs', TEXT.subtitle)}>{executableStepCount} 个启用步骤</div>
                </div>
                <div className="space-y-2 border-t pt-3 text-xs">
                  <div className="flex justify-between gap-3">
                    <span>设备与节点在线状态</span>
                    <span className={blockedCount === 0 && devices.length > 0 ? 'text-success' : 'text-destructive'}>
                      {blockedCount === 0 && devices.length > 0 ? `${readyCount} 台通过` : `${blockedCount} 台阻塞`}
                    </span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span>版本与型号一致性</span>
                    <span className={warnings.length ? 'text-warning' : 'text-success'}>
                      {warnings.length ? '存在提醒' : '通过'}
                    </span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span>所选节点当前活跃任务</span>
                    <span className={TEXT.subtitle}>{selectedHostActiveJobs} 个</span>
                  </div>
                </div>
                {blockedCount > 0 ? (
                  <Button type="button" variant="outline" className="w-full" onClick={onRemoveBlocked}>
                    <Trash2 className="mr-2 h-4 w-4" />移除全部阻塞设备
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          </div>
        </div>

        <Card>
          <CardContent className="pt-5">
            <label htmlFor="plan-execute-note" className={cn('text-xs font-medium', TEXT.subtitle)}>
              执行备注（选填）
            </label>
            <textarea
              id="plan-execute-note"
              value={note}
              onChange={(event) => onNoteChange(event.target.value.slice(0, 500))}
              rows={2}
              maxLength={500}
              placeholder="记录本次发起目的、批次或关注点"
              className="mt-2 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className={cn('mt-1 text-right text-[11px]', TEXT.subtitle)}>{note.length}/500</div>
          </CardContent>
        </Card>
      </div>
    </TooltipProvider>
  );
}
