import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  ExternalLink,
  FileText,
  FileWarning,
  Loader2,
  LogOut,
  RotateCw,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from '@/components/ui/sheet';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ALERT_BOX, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  DisarmResultEntry,
  PlanRunJobDetail,
} from '@/utils/api/types';
import { formatScriptIdentity } from './scriptIdentity';
import {
  finishingKindLabel,
  finishingTriggerLabel,
  formatFinishingDeadlineCountdown,
} from './planRunStatus';
import { useFinishingClock } from './useFinishingClock';
import { formatDateTimeShort } from '@/utils/format';
import {
  DEVICE_LINK_STATUS,
  isDeviceLinkReachable,
} from '@/components/plan-run/deviceLinkStatus';
import { disarmResultLabel, familyLabel } from '@/components/device/deviceReturn';

interface Props {
  device: DeviceMatrixItem | PlanRunJobDetail | null;
  runId: number;
  /** B3-3：显式打开态与 jobId，支持 `?drawer=` 深链加载态与错误态展示 */
  open?: boolean;
  jobId?: number | null;
  isLoading?: boolean;
  isError?: boolean;
  onRetryLoad?: () => void;
  onClose: () => void;
  onManualRetry: (jobId: number) => void;
  onManualExit: (jobId: number) => void;
  onOpenReport: (jobId: number) => void;
  isRetryPending?: boolean;
  isExitPending?: boolean;
  /** U5-3 / B3-3：筛选列表内切机导航；表格分页模式下传 `本页` 明确标注页边界 */
  devicePosition?: number;
  totalDevices?: number;
  navScopeLabel?: string;
  onPrevDevice?: () => void;
  onNextDevice?: () => void;
}

const BUSY_REASON_LABELS: Record<string, string> = {
  active_lease: '设备租约占用',
  device_offline: '设备离线',
  host_offline: '主机离线',
  adb_excluded: 'ADB 状态排除',
};

function busyReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—';
  return BUSY_REASON_LABELS[reason] ?? reason;
}

const TERMINAL_DEVICE: ReadonlyArray<DeviceUiStatus> = [
  'completed',
  'failed',
  'aborted',
  'unknown',
];

export default function DeviceDetailDrawer(props: Props) {
  const effectiveJobId = props.device?.job_id ?? props.jobId ?? 0;
  // B3-3（契约 §5.3 规则 4）：按 `(runId, jobId)` 严格隔离抽屉内操作态与折叠态，
  // 切换设备时绝不残留前一台的确认弹窗、复制提示或展开子查询。
  return (
    <DeviceDetailDrawerInner
      key={`${props.runId}:${effectiveJobId}`}
      {...props}
    />
  );
}

function DeviceDetailDrawerInner({
  device,
  runId,
  open,
  jobId,
  isLoading = false,
  isError = false,
  onRetryLoad,
  onClose,
  onManualRetry,
  onManualExit,
  onOpenReport,
  isRetryPending = false,
  isExitPending = false,
  devicePosition,
  totalDevices,
  navScopeLabel,
  onPrevDevice,
  onNextDevice,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState<null | 'retry' | 'exit'>(null);
  const [copiedSerial, setCopiedSerial] = useState(false);

  const isOpen = open ?? Boolean(device);
  const effectiveJobId = device?.job_id ?? jobId ?? null;
  const isTerminal = device ? TERMINAL_DEVICE.includes(device.ui_status) : false;
  const exitRequested = device?.manual_action === 'EXIT_REQUESTED';
  const retryRequested = device?.manual_action === 'RETRY_NOW';
  const linkStatus = device?.device_link_status;
  const execStatus = (device?.job_exec_status ?? device?.ui_status ?? 'unknown') as DeviceUiStatus;
  const finishingDeadline = isOpen && execStatus === 'finishing' ? device?.finishing_deadline_at : null;
  const finishingDeadlines = useMemo(() => finishingDeadline ? [finishingDeadline] : [], [finishingDeadline]);
  const finishingNow = useFinishingClock(finishingDeadlines);
  const finishingCountdown = formatFinishingDeadlineCountdown(finishingDeadline, finishingNow);
  const linkReachable =
    linkStatus == null || isDeviceLinkReachable(linkStatus);
  // Why: 「设备不可达」必须只有一处判定并直接否决 canManualRetry。
  //      两者各自算时,unauthorized 会让 capabilities 放行、link 判 adb_error,
  //      于是警告条和「立即重试」按钮同时渲染,点下去后端还真执行。
  const retryBlockedByDevice =
    device?.job_status === 'RUNNING' &&
    (device.capabilities?.manual_retry_blocked_reason === 'device_disconnected' ||
      !linkReachable);
  const canManualRetry =
    !retryBlockedByDevice &&
    (device?.capabilities
      ? device.capabilities.manual_retry === true
      : !isTerminal && linkReachable);
  const canManualExit = device?.capabilities
    ? device.capabilities.manual_exit === true
    : !isTerminal;
  const canOpenReport = device?.capabilities?.open_report ?? true;

  const deviceSerialLabel = device?.device_serial || (device ? `Device #${device.device_id}` : '');
  const hostDisplayLabel =
    (device && 'host_name' in device && device.host_name) || device?.host_id || '—';
  const logsHref = device?.device_serial
    ? `/execution/plan-runs/${runId}/logs?q=${encodeURIComponent(device.device_serial)}`
    : `/execution/plan-runs/${runId}/logs`;
  const aiDiagnoseHref = device
    ? `/assistant?prompt=${encodeURIComponent(
        `请协助诊断 PlanRun #${runId} 中的设备 ${deviceSerialLabel}（Job #${device.job_id}，执行态=${execStatus}，连接态=${linkStatus ?? 'unknown'}${device.status_reason ? `，原因=${device.status_reason}` : ''}）`,
      )}`
    : '/assistant';

  const prevLabel = navScopeLabel ? `上一台设备（${navScopeLabel}）` : '上一台设备';
  const nextLabel = navScopeLabel ? `下一台设备（${navScopeLabel}）` : '下一台设备';
  const positionText =
    devicePosition != null && totalDevices != null
      ? navScopeLabel
        ? `${navScopeLabel} ${devicePosition}/${totalDevices}`
        : `${devicePosition}/${totalDevices}`
      : null;

  const handleCopySerial = () => {
    if (!device?.device_serial || !navigator.clipboard?.writeText) return;
    void navigator.clipboard.writeText(device.device_serial).then(() => {
      setCopiedSerial(true);
      window.setTimeout(() => setCopiedSerial(false), 1500);
    });
  };

  return (
    <>
      <Sheet
        open={isOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && confirmOpen === null) onClose();
        }}
      >
        {device ? (
          <SheetContent
            side="right"
            size="lg"
            hideCloseButton
            overlayTestId="device-drawer-overlay"
            data-testid="device-drawer"
            aria-label={`设备 ${deviceSerialLabel} 详情`}
            onEscapeKeyDown={(event) => {
              if (confirmOpen !== null) event.preventDefault();
            }}
            onInteractOutside={(event) => {
              if (confirmOpen !== null) event.preventDefault();
            }}
          >
            {/* Header */}
            <header className="flex items-center justify-between gap-3 border-b px-5 py-3.5">
              <div className="min-w-0">
                <SheetDescription className={cn('truncate text-xs', TEXT.subtitle)}>
                  Job #{device.job_id} · {hostDisplayLabel}
                  {device.device_model ? ` · ${device.device_model}` : ''}
                </SheetDescription>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <SheetTitle className={cn('break-all font-mono text-base font-semibold', TEXT.heading)}>
                    {deviceSerialLabel}
                  </SheetTitle>
                  {device.device_serial && (
                    <button
                      type="button"
                      data-testid="device-drawer-copy-serial"
                      onClick={handleCopySerial}
                      aria-label="复制设备串号"
                      title="复制设备串号"
                      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded border border-border/70 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      {copiedSerial ? (
                        <Check className="h-3.5 w-3.5 text-success" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                    </button>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {totalDevices != null && totalDevices > 1 && positionText != null && (
                  <div
                    className="inline-flex items-center rounded-md border border-border bg-background"
                    aria-label="切换设备"
                  >
                    <button
                      type="button"
                      data-testid="device-drawer-prev"
                      onClick={onPrevDevice}
                      disabled={!onPrevDevice}
                      aria-label={prevLabel}
                      title={prevLabel}
                      className="px-1.5 py-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-35"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" />
                    </button>
                    <span
                      data-testid="device-drawer-position"
                      className="border-x border-border px-2 font-mono text-xs tabular-nums text-muted-foreground"
                    >
                      {positionText}
                    </span>
                    <button
                      type="button"
                      data-testid="device-drawer-next"
                      onClick={onNextDevice}
                      disabled={!onNextDevice}
                      aria-label={nextLabel}
                      title={nextLabel}
                      className="px-1.5 py-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-35"
                    >
                      <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onClose}
                  data-testid="device-drawer-close"
                  aria-label="关闭设备详情"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </header>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm">
          <div
            data-testid="device-drawer-status-pill"
            className="mb-3 flex flex-wrap items-center gap-1.5"
          >
            <StatusBadge
              kind="device-link"
              status={linkStatus ?? 'unknown'}
              size="sm"
            />
            <StatusBadge
              kind="device-ui"
              status={execStatus}
              size="sm"
              spin={execStatus === 'running'}
            />
            {execStatus === 'finishing' && finishingKindLabel(device.finishing_kind) && (
              <span
                className={cn('text-xs font-semibold', TEXT.subtitle)}
                data-testid="device-drawer-finishing-kind"
              >
                · {finishingKindLabel(device.finishing_kind)}
              </span>
            )}
            <span className={cn('text-xs font-semibold', TEXT.subtitle)}>
              · {device.current_stage.toUpperCase()}
            </span>
          </div>

          {retryBlockedByDevice && (
            <div
              className={cn('mb-3 rounded-lg border-l-4 border-info px-3 py-2 text-xs', ALERT_BOX.warning)}
              data-testid="device-drawer-offline-hint"
            >
              <div className="font-semibold">设备 ADB 不可达</div>
              <p className="mt-0.5 opacity-90">
                {DEVICE_LINK_STATUS[linkStatus ?? 'unknown']?.hint ??
                  '立即重试无法恢复 ADB 连接，请先检查 USB 或重启设备。'}
              </p>
            </div>
          )}

          <KvList
            rows={[
              ['当前步骤', device.current_step || '—', true],
              // #3350（ADR-0023 D3）：脚本身份（脚本@版本）；旧快照查不到 → —
              [
                '脚本',
                formatScriptIdentity(
                  device.current_script_name,
                  device.current_script_version,
                ) || '—',
                true,
              ],
              ['Job 状态', device.job_status, false],
              ...(device.adb_state || device.adb_connected != null
                ? [[
                    'ADB',
                    `${device.adb_connected ? 'connected' : 'disconnected'} · ${device.adb_state || '—'}`,
                    false,
                  ] as KvRow]
                : []),
              ...(device.status_reason
                ? [[
                    '状态原因',
                    device.status_reason,
                    false,
                    device.ui_status === 'failed'
                      ? 'text-destructive-text font-semibold'
                      : 'text-warning font-semibold',
                    true,
                  ] as KvRow]
                : []),
              ...(device.grace_remaining_seconds != null
                ? [['Grace 剩余', `${device.grace_remaining_seconds}s`, false] as KvRow]
                : []),
              ...(device.pending_claim_remaining_seconds != null
                ? [['认领 SLA 剩余', `${device.pending_claim_remaining_seconds}s`, false] as KvRow]
                : []),
              ...(execStatus === 'finishing' && finishingKindLabel(device.finishing_kind)
                ? [['收尾子类型', finishingKindLabel(device.finishing_kind)!, false] as KvRow]
                : []),
              ...(execStatus === 'finishing' && finishingTriggerLabel(device.finishing_trigger)
                ? [['收尾触发', finishingTriggerLabel(device.finishing_trigger)!, false] as KvRow]
                : []),
              ...(finishingCountdown != null
                ? [['收尾截止', finishingCountdown, false] as KvRow]
                : []),
              ...(device.busy_reason
                ? [[
                    'BUSY 来源',
                    busyReasonLabel(device.busy_reason),
                    false,
                    'text-warning font-semibold',
                  ] as KvRow]
                : []),
              ...(device.busy_lease_job_id != null
                ? [['占用 Job', `#${device.busy_lease_job_id}`, false] as KvRow]
                : []),
              ['巡检周期', `#${device.patrol_cycle_count}`, false],
              ['周期成功 / 失败', `${device.patrol_success_cycle_count} / ${device.patrol_failed_cycle_count}`, false],
              ['连续失败连击', String(device.current_failure_streak), false],
              ['下次重试', formatKvTime(device.next_retry_at), false],
              ['手动操作', device.manual_action || '—', false],
              ['Watcher 异常计数', String(device.log_signal_count), false],
              ['最近心跳', formatKvTime(device.last_heartbeat_at), false],
              ['开始时间', formatKvTime(device.started_at), false],
              ['结束时间', formatKvTime(device.ended_at), false],
            ]}
          />

          <DisarmResultsBlock results={device.disarm_results} />

          {device.log_signal_count > 0 && (
            <div className={cn('mt-4 rounded-lg border-l-4 border-warning px-3 py-2 text-xs', ALERT_BOX.warning)}>
              <div className="font-semibold">检测到 {device.log_signal_count} 条 Watcher 异常</div>
              <p className="mt-0.5 text-xs opacity-90">
                可在「异常与进度」页签的异常仪表盘查看包名归因，或直接查看该设备执行日志。
              </p>
            </div>
          )}

          {/* B3-3（契约 §5.3 规则 3）：按需延迟加载的阶段时间线与 Watcher 异常明细（默认折叠，绝不预取） */}
          {device.job_id && (
            <JobTimelineBlock
              runId={runId}
              jobId={device.job_id}
              traceCount={'step_trace_count' in device ? device.step_trace_count : undefined}
            />
          )}

          {device.job_id && device.log_signal_count > 0 && (
            <JobWatcherSignalsBlock
              runId={runId}
              jobId={device.job_id}
              signalCount={device.log_signal_count}
            />
          )}

          {/* ADR-0025 Sprint 3: Crash 产物（AEE/bugreport 等 JobArtifact） */}
          {device.job_id && (
            <CrashArtifactsBlock runId={runId} jobId={device.job_id} />
          )}

          {(retryRequested || exitRequested) && (
            <div className="mt-4 rounded-lg border-l-4 border-primary bg-primary/5 px-3 py-2 text-xs text-primary">
              <div className="font-semibold">
                {retryRequested ? '已请求立即重试' : '已请求退出该设备'}
              </div>
              <p className="mt-0.5 text-xs opacity-90">
                Agent 将在下一次心跳处理(通常 10s 内)
              </p>
            </div>
          )}
        </div>

        {/* Footer — actions */}
        <footer className="space-y-2 border-t bg-muted/50 px-5 py-3">
          {(canManualRetry || canManualExit) && (
            <div className="grid grid-cols-2 gap-2">
              {canManualRetry ? (
                <Button
                  variant="outline"
                  size="sm"
                  data-testid="device-drawer-retry-btn"
                  disabled={isRetryPending || retryRequested}
                  onClick={() => setConfirmOpen('retry')}
                >
                  {isRetryPending ? (
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RotateCw className="mr-1 h-3.5 w-3.5" />
                  )}
                  立即重试
                </Button>
              ) : null}
              {canManualExit && (
                <Button
                  variant="outline"
                  size="sm"
                  data-testid="device-drawer-exit-btn"
                  disabled={isExitPending || exitRequested}
                  onClick={() => setConfirmOpen('exit')}
                  className="border-destructive/30 text-destructive-text hover:bg-destructive/10"
                >
                  {isExitPending ? (
                    <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <LogOut className="mr-1 h-3.5 w-3.5" />
                  )}
                  退出该设备
                </Button>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={aiDiagnoseHref} data-testid="device-drawer-ai-btn">
                  <Sparkles className="mr-1 h-3.5 w-3.5 text-primary" />
                  交给 AI 诊断
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={logsHref} data-testid="device-drawer-logs-btn">
                  <FileText className="mr-1 h-3.5 w-3.5" />
                  单机日志
                </a>
              </Button>
            </div>
            {canOpenReport && (
              <Button
                variant="outline"
                size="sm"
                data-testid="device-drawer-open-report"
                onClick={() => onOpenReport(device.job_id)}
              >
                <ExternalLink className="mr-1 h-3.5 w-3.5" />
                查看 Job 报告
              </Button>
            )}
          </div>
        </footer>
          </SheetContent>
        ) : isOpen ? (
          <SheetContent
            side="right"
            size="lg"
            hideCloseButton
            overlayTestId="device-drawer-overlay"
            data-testid="device-drawer"
            aria-label={effectiveJobId ? `Job #${effectiveJobId} 详情` : '设备详情'}
          >
            <header className="flex items-center justify-between gap-3 border-b px-5 py-3.5">
              <div className="min-w-0">
                <SheetDescription className={cn('text-xs', TEXT.subtitle)}>
                  PlanRun #{runId}
                </SheetDescription>
                <SheetTitle className={cn('font-mono text-base font-semibold', TEXT.heading)}>
                  {effectiveJobId ? `Job #${effectiveJobId}` : '设备详情'}
                </SheetTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="device-drawer-close"
                aria-label="关闭设备详情"
              >
                <X className="h-4 w-4" />
              </Button>
            </header>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {isError ? (
                <div
                  data-testid="device-drawer-error"
                  className="flex flex-col items-center justify-center gap-2 py-12 text-center text-xs"
                >
                  <AlertCircle className="h-6 w-6 text-destructive/70" />
                  <div className="font-semibold text-destructive">加载单机详情失败</div>
                  <p className={TEXT.subtitle}>Job 可能不存在或不属于当前 PlanRun。</p>
                  {onRetryLoad && (
                    <Button variant="outline" size="sm" onClick={onRetryLoad}>
                      重试
                    </Button>
                  )}
                </div>
              ) : (
                <div data-testid="device-drawer-loading" className="space-y-3">
                  <Skeleton className="h-6 w-48" />
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                  {isLoading ? null : null}
                </div>
              )}
            </div>
          </SheetContent>
        ) : null}
      </Sheet>

      <AlertDialog
        open={confirmOpen !== null && Boolean(device)}
        onOpenChange={(o) => !o && setConfirmOpen(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmOpen === 'retry' ? '立即重试该设备?' : '退出该设备?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {/* 断连时按钮已被 canManualRetry 否决,不会走到 retry 分支 */}
              {confirmOpen === 'retry'
                ? '将清除该设备的退避等待并请求 Agent 在下一次心跳重新执行 patrol;不会重置 current_failure_streak,以保留诊断信息。'
                : '请求 Agent 在下一次心跳跳过剩余 patrol 并 abort 该 Job(不执行 teardown)。设备会回到资源池,该设备不会再产出 patrol 数据。'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="device-drawer-confirm"
              onClick={() => {
                if (device && confirmOpen === 'retry') onManualRetry(device.job_id);
                else if (device && confirmOpen === 'exit') onManualExit(device.job_id);
                setConfirmOpen(null);
              }}
              className={
                confirmOpen === 'exit' ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90' : ''
              }
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/**
 * ADR-0061 D4.4 / #3723 U6：按专项展示本 job 的解除结果。
 *
 * 空/缺省 = 「无解除记录」（激活前或该 job 未建立足迹），不得报错；
 * 未知机器值原样显示，避免新枚举让整块消失。
 */
function DisarmResultsBlock({ results }: { results?: Record<string, DisarmResultEntry> | null }) {
  const entries = Object.entries(results ?? {});
  return (
    <div className="mt-4" data-testid="device-drawer-disarm-results">
      <div className={cn('mb-1 text-xs font-semibold', TEXT.body)}>解除结果</div>
      {entries.length === 0 ? (
        <p className={cn('text-xs', TEXT.subtitle)} data-testid="device-drawer-disarm-empty">
          无解除记录
        </p>
      ) : (
        <ul className="space-y-1">
          {entries.map(([family, entry]) => (
            <li
              key={family}
              className="rounded border px-2 py-1 text-xs"
              data-testid={'device-drawer-disarm-' + family}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{familyLabel(family)}</span>
                <span className="font-mono text-foreground">
                  {entry?.result == null ? '—' : disarmResultLabel(String(entry.result))}
                </span>
              </div>
              {entry?.reason ? (
                <p className={cn('mt-0.5 whitespace-pre-wrap break-words', TEXT.subtitle)}>
                  原因：{entry.reason}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** [label, value, mono, extraCls?, wrapFull?] — wrapFull disables ellipsis truncation */
type KvRow = [string, string, boolean, string?, boolean?];

/**
 * 时间类 KV 的本地化口径（#2420）：抽屉里原先直接铺后端裸 ISO（
 * `2026-09-16T11:49:18.577637+00:00`），而**同一页面页头**显示 `09/16 19:48`
 * —— 一屏两套口径，差 8 小时。改走页头同一个 `formatDateTimeShort`，
 * 于是"抽屉里的时间"和"页头的时间"是同一个规则（同族缺陷见 #2265/#2358）。
 */
function formatKvTime(value?: string | null): string {
  return value ? formatDateTimeShort(value) : '—';
}

function KvList({ rows }: { rows: KvRow[] }) {
  return (
    <dl className="divide-y rounded-lg border">
      {rows.map(([k, v, mono, extraCls, wrapFull]) => (
        <div
          key={k}
          className={cn(
            'flex justify-between gap-3 px-3 py-1.5 text-xs',
            wrapFull ? 'items-start' : 'items-center',
          )}
        >
          <dt className={cn('shrink-0', TEXT.subtitle, extraCls || '')}>{k}</dt>
          <dd
            data-testid={k === '状态原因' ? 'device-drawer-status-reason' : undefined}
            className={cn(
              'max-w-[70%] text-right',
              wrapFull ? 'whitespace-pre-wrap break-words' : 'truncate',
              TEXT.body,
              mono && 'font-mono',
              extraCls || '',
            )}
            title={wrapFull ? undefined : v}
          >
            {v}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const CRASH_ARTIFACT_TYPES = ['aee_crash', 'vendor_aee_crash', 'bugreport'];
const AEE_ARTIFACT_TYPES = new Set(['aee_crash', 'vendor_aee_crash']);
const NON_ISSUE_AEE_EXACT_NAMES = new Set([
  'aee.lck',
  'db_history',
  'file_tree.txt',
  'swt_jbt_traces',
  'temp',
]);
const NON_ISSUE_AEE_SUFFIXES = ['.lck', '.lock', '.tmp', '.temp'];

function isNonIssueAeeArtifactFilename(filename: string | null | undefined): boolean {
  if (!filename) return false;
  const base = filename.split('/').pop()?.trim() ?? '';
  if (!base) return false;
  const normalized = base.replace(/^(?:\d+_)+/, '').toLowerCase();
  if (!normalized) return false;
  if (NON_ISSUE_AEE_EXACT_NAMES.has(normalized)) return true;
  if (NON_ISSUE_AEE_SUFFIXES.some((suffix) => normalized.endsWith(suffix))) return true;
  if (normalized.startsWith('db_history.') || normalized.startsWith('swt_jbt_traces')) {
    return true;
  }
  return false;
}

const ARTIFACT_LABELS: Record<string, string> = {
  aee_crash: 'AEE',
  vendor_aee_crash: 'Vendor AEE',
  bugreport: 'Bugreport',
};

function JobTimelineBlock({
  runId,
  jobId,
  traceCount,
}: {
  runId: number;
  jobId: number;
  traceCount?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const timelineQ = useQuery({
    queryKey: planRunKeys.jobTimeline(runId, jobId, { skip: 0, limit: 50 }),
    queryFn: () => api.planRuns.getJobTimeline(runId, jobId, { skip: 0, limit: 50 }),
    enabled: expanded && !!runId && !!jobId,
    staleTime: 15_000,
  });

  return (
    <div className="mt-4 rounded-lg border border-border/80" data-testid="device-drawer-timeline-block">
      <button
        type="button"
        data-testid="device-drawer-timeline-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-semibold hover:bg-muted/40"
      >
        <span className="inline-flex items-center gap-1.5">
          <Clock className="h-3.5 w-3.5 text-muted-foreground" />
          阶段时间线
          {typeof traceCount === 'number' ? (
            <span className={cn('font-mono font-normal', TEXT.subtitle)}>({traceCount})</span>
          ) : null}
        </span>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-border/70 px-3 py-2 text-xs">
          {timelineQ.isLoading ? (
            <div className={cn('flex items-center gap-1.5', TEXT.subtitle)}>
              <Loader2 className="h-3 w-3 animate-spin" /> 加载时间线...
            </div>
          ) : timelineQ.isError ? (
            <div className="text-destructive-text">时间线加载失败</div>
          ) : !timelineQ.data?.items?.length ? (
            <div className={TEXT.subtitle}>暂无步骤事件记录</div>
          ) : (
            <div className="space-y-1.5">
              <div className={cn('text-[11px]', TEXT.subtitle)}>
                展示 {timelineQ.data.items.length} / 共 {timelineQ.data.total} 条
              </div>
              <ul className="max-h-56 space-y-1 overflow-y-auto">
                {timelineQ.data.items.map((item) => (
                  <li key={item.id} className="rounded border border-border/60 px-2 py-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-[11px] font-medium">
                        {item.stage} · {item.step_id}
                      </span>
                      <span className={cn('shrink-0 font-mono text-[10px]', TEXT.subtitle)}>
                        {item.status} · {formatKvTime(item.original_ts)}
                      </span>
                    </div>
                    {item.error_message ? (
                      <p className="mt-0.5 break-words text-[11px] text-destructive-text">
                        {item.error_message}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function JobWatcherSignalsBlock({
  runId,
  jobId,
  signalCount,
}: {
  runId: number;
  jobId: number;
  signalCount: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const signalsQ = useQuery({
    queryKey: planRunKeys.jobWatcherSignals(runId, jobId, { skip: 0, limit: 50 }),
    queryFn: () => api.planRuns.getJobWatcherSignals(runId, jobId, { skip: 0, limit: 50 }),
    enabled: expanded && !!runId && !!jobId,
    staleTime: 15_000,
  });

  return (
    <div className="mt-3 rounded-lg border border-border/80" data-testid="device-drawer-signals-block">
      <button
        type="button"
        data-testid="device-drawer-signals-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-semibold hover:bg-muted/40"
      >
        <span className="inline-flex items-center gap-1.5">
          <ShieldAlert className="h-3.5 w-3.5 text-warning" />
          Watcher 异常明细
          <span className={cn('font-mono font-normal', TEXT.subtitle)}>({signalCount})</span>
        </span>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-border/70 px-3 py-2 text-xs">
          {signalsQ.isLoading ? (
            <div className={cn('flex items-center gap-1.5', TEXT.subtitle)}>
              <Loader2 className="h-3 w-3 animate-spin" /> 加载异常明细...
            </div>
          ) : signalsQ.isError ? (
            <div className="text-destructive-text">异常明细加载失败</div>
          ) : !signalsQ.data?.items?.length ? (
            <div className={TEXT.subtitle}>暂无明细记录</div>
          ) : (
            <div className="space-y-1.5">
              <div className={cn('text-[11px]', TEXT.subtitle)}>
                展示 {signalsQ.data.items.length} / 共 {signalsQ.data.total} 条
              </div>
              <ul className="max-h-56 space-y-1 overflow-y-auto">
                {signalsQ.data.items.map((sig) => (
                  <li key={sig.id} className="rounded border border-border/60 px-2 py-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[11px] font-medium text-warning">
                        {sig.category}
                        <span className={cn('ml-1.5 font-normal', TEXT.subtitle)}>({sig.source})</span>
                      </span>
                      <span className={cn('shrink-0 font-mono text-[10px]', TEXT.subtitle)}>
                        {formatKvTime(sig.detected_at)}
                      </span>
                    </div>
                    <div className={cn('mt-0.5 truncate font-mono text-[10px]', TEXT.subtitle)} title={sig.path_on_device}>
                      {sig.path_on_device}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CrashArtifactsBlock({ runId, jobId }: { runId: number; jobId: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['job-artifacts', runId, jobId],
    queryFn: () => api.planRuns.listJobArtifacts(runId, jobId),
    enabled: !!jobId,
    staleTime: 30_000,
  });

  const crashArtifacts = (data || []).filter((a) => {
    if (!CRASH_ARTIFACT_TYPES.includes(a.artifact_type)) return false;
    if (AEE_ARTIFACT_TYPES.has(a.artifact_type) && isNonIssueAeeArtifactFilename(a.filename)) {
      return false;
    }
    return true;
  });

  if (isLoading) {
    return (
      <div className={cn('mt-4 flex items-center gap-1.5 text-xs', TEXT.subtitle)}>
        <Loader2 className="h-3 w-3 animate-spin" /> 加载 Crash 产物...
      </div>
    );
  }
  if (isError || crashArtifacts.length === 0) return null;

  return (
    <div className="mt-4" data-testid="crash-artifacts-block">
      <div className={cn('mb-1 flex items-center gap-1 text-xs font-semibold', TEXT.body)}>
        <FileWarning className="h-3.5 w-3.5 text-warning" />
        Crash 产物（{crashArtifacts.length}）
      </div>
      <div className="space-y-1">
        {crashArtifacts.map((a) => (
          <div key={a.id} className="flex items-center gap-2 rounded border px-2 py-1 text-[11px]">
            <span className={cn('font-mono', TEXT.subtitle)}>
              {ARTIFACT_LABELS[a.artifact_type] || a.artifact_type}
            </span>
            <a
              href={`/api/v1/plan-runs/${runId}/jobs/${jobId}/artifacts/${a.id}/download`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
              data-testid="crash-artifact-download"
            >
              <ExternalLink className="h-3 w-3" /> 下载
            </a>
            {a.filename && (
              <span className={cn('flex-1 truncate font-mono text-muted-foreground/70', TEXT.subtitle)} title={a.filename}>
                {a.filename}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
