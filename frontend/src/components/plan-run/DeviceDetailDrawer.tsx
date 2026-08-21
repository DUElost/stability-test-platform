import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  X,
  RotateCw,
  LogOut,
  ExternalLink,
  Loader2,
  FileWarning,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
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
import { ALERT_BOX, DRAWER, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { api } from '@/utils/api';
import type { DeviceMatrixItem, DeviceUiStatus } from '@/utils/api/types';
import {
  DEVICE_LINK_STATUS,
  isDeviceLinkReachable,
} from '@/components/plan-run/deviceLinkStatus';

interface Props {
  device: DeviceMatrixItem | null;
  runId: number;
  onClose: () => void;
  onManualRetry: (jobId: number) => void;
  onManualExit: (jobId: number) => void;
  onOpenReport: (jobId: number) => void;
  isRetryPending?: boolean;
  isExitPending?: boolean;
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

export default function DeviceDetailDrawer({
  device,
  runId,
  onClose,
  onManualRetry,
  onManualExit,
  onOpenReport,
  isRetryPending = false,
  isExitPending = false,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState<null | 'retry' | 'exit'>(null);
  const drawerRef = useRef<HTMLElement>(null);

  // Close on Escape (unless a confirm dialog is open — let it handle Esc first),
  // and move focus into the drawer when it opens for keyboard / screen-reader users.
  useEffect(() => {
    if (!device) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && confirmOpen === null) onClose();
    };
    document.addEventListener('keydown', onKey);
    drawerRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [device, confirmOpen, onClose]);

  if (!device) return null;

  const isTerminal = TERMINAL_DEVICE.includes(device.ui_status);
  const exitRequested = device.manual_action === 'EXIT_REQUESTED';
  const retryRequested = device.manual_action === 'RETRY_NOW';
  const linkStatus = device.device_link_status;
  const execStatus = (device.job_exec_status ?? device.ui_status) as DeviceUiStatus;
  const linkReachable =
    linkStatus == null || isDeviceLinkReachable(linkStatus);
  // Why: 「设备不可达」必须只有一处判定并直接否决 canManualRetry。
  //      两者各自算时,unauthorized 会让 capabilities 放行、link 判 adb_error,
  //      于是警告条和「立即重试」按钮同时渲染,点下去后端还真执行。
  const retryBlockedByDevice =
    device.job_status === 'RUNNING' &&
    (device.capabilities?.manual_retry_blocked_reason === 'device_disconnected' ||
      !linkReachable);
  const canManualRetry =
    !retryBlockedByDevice &&
    (device.capabilities
      ? device.capabilities.manual_retry === true
      : !isTerminal && linkReachable);
  const canManualExit = device.capabilities
    ? device.capabilities.manual_exit === true
    : !isTerminal;
  const canOpenReport = device.capabilities?.open_report ?? true;

  return (
    <>
      {/* Overlay */}
      <div
        data-testid="device-drawer-overlay"
        onClick={onClose}
        className={DRAWER.overlay}
      />

      {/* Drawer */}
      <aside
        ref={drawerRef}
        data-testid="device-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`设备 ${device.device_serial || `#${device.device_id}`} 详情`}
        tabIndex={-1}
        className={DRAWER.panel}
      >
        {/* Header */}
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="min-w-0">
            <p className={cn('truncate text-xs', TEXT.subtitle)}>
              Job #{device.job_id} · {device.host_id || '—'}
            </p>
            <h2 className={cn('truncate font-mono text-base font-semibold', TEXT.heading)}>
              {device.device_serial || `Device #${device.device_id}`}
            </h2>
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

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto px-4 py-3 text-sm">
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
                      ? 'text-destructive font-semibold'
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
              ['下次重试', device.next_retry_at || '—', false],
              ['手动操作', device.manual_action || '—', false],
              ['Watcher 异常计数', String(device.log_signal_count), false],
              ['最近心跳', device.last_heartbeat_at || '—', false],
              ['开始时间', device.started_at || '—', false],
              ['结束时间', device.ended_at || '—', false],
            ]}
          />

          {device.log_signal_count > 0 && (
            <div className={cn('mt-4 rounded-lg border-l-4 border-warning px-3 py-2 text-xs', ALERT_BOX.warning)}>
              <div className="font-semibold">检测到 {device.log_signal_count} 条 Watcher 异常</div>
              <p className="mt-0.5 text-xs opacity-90">
                明细见上方"业务流时间线"事件流(stage = patrol, severity = 异常)
              </p>
            </div>
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
        <footer className="grid grid-cols-2 gap-2 border-t bg-muted/50 px-4 py-3">
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
              className="border-destructive/30 text-destructive hover:bg-destructive/10"
            >
              {isExitPending ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <LogOut className="mr-1 h-3.5 w-3.5" />
              )}
              退出该设备
            </Button>
          )}
          {canOpenReport && (
            <Button
              variant="outline"
              size="sm"
              className="col-span-2"
              data-testid="device-drawer-open-report"
              onClick={() => onOpenReport(device.job_id)}
            >
              <ExternalLink className="mr-1 h-3.5 w-3.5" />
              查看 Job 报告
            </Button>
          )}
        </footer>
      </aside>

      <AlertDialog
        open={confirmOpen !== null}
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
                if (confirmOpen === 'retry') onManualRetry(device.job_id);
                else if (confirmOpen === 'exit') onManualExit(device.job_id);
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

/** [label, value, mono, extraCls?, wrapFull?] — wrapFull disables ellipsis truncation */
type KvRow = [string, string, boolean, string?, boolean?];

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
const ARTIFACT_LABELS: Record<string, string> = {
  aee_crash: 'AEE',
  vendor_aee_crash: 'Vendor AEE',
  bugreport: 'Bugreport',
};

function CrashArtifactsBlock({ runId, jobId }: { runId: number; jobId: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['job-artifacts', runId, jobId],
    queryFn: () => api.planRuns.listJobArtifacts(runId, jobId),
    enabled: !!jobId,
    staleTime: 30_000,
  });

  const crashArtifacts = (data || []).filter(
    (a) => CRASH_ARTIFACT_TYPES.includes(a.artifact_type),
  );

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
