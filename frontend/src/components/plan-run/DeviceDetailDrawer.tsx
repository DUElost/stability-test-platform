import { useState } from 'react';
import {
  X,
  RotateCw,
  LogOut,
  ExternalLink,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  PauseCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
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
import type { DeviceMatrixItem, DeviceUiStatus } from '@/utils/api/types';

interface Props {
  device: DeviceMatrixItem | null;
  onClose: () => void;
  onManualRetry: (jobId: number) => void;
  onManualExit: (jobId: number) => void;
  onOpenReport: (jobId: number) => void;
  isRetryPending?: boolean;
  isExitPending?: boolean;
}

const STATUS_PILL: Record<DeviceUiStatus, { cls: string; Icon: React.ElementType; label: string }> = {
  running: { cls: 'bg-orange-100 text-orange-800', Icon: Loader2, label: '运行中' },
  completed: { cls: 'bg-green-100 text-green-800', Icon: CheckCircle2, label: '完成' },
  failed: { cls: 'bg-red-100 text-red-800', Icon: XCircle, label: '失败' },
  risk: { cls: 'bg-amber-100 text-amber-800', Icon: AlertTriangle, label: '风险' },
  backoff: { cls: 'bg-purple-100 text-purple-800', Icon: Clock, label: '退避' },
  pending: { cls: 'bg-gray-100 text-gray-700', Icon: PauseCircle, label: '等待' },
};

const TERMINAL_DEVICE: ReadonlyArray<DeviceUiStatus> = ['completed', 'failed'];

export default function DeviceDetailDrawer({
  device,
  onClose,
  onManualRetry,
  onManualExit,
  onOpenReport,
  isRetryPending = false,
  isExitPending = false,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState<null | 'retry' | 'exit'>(null);

  if (!device) return null;

  const cfg = STATUS_PILL[device.ui_status];
  const Icon = cfg.Icon;
  const isTerminal = TERMINAL_DEVICE.includes(device.ui_status);
  const exitRequested = device.manual_action === 'EXIT_REQUESTED';
  const retryRequested = device.manual_action === 'RETRY_NOW';

  return (
    <>
      {/* Overlay */}
      <div
        data-testid="device-drawer-overlay"
        onClick={onClose}
        className="fixed inset-0 z-30 bg-black/30 backdrop-blur-sm"
      />

      {/* Drawer */}
      <aside
        data-testid="device-drawer"
        className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col overflow-hidden border-l bg-white shadow-2xl"
      >
        {/* Header */}
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-xs text-gray-500">
              Job #{device.job_id} · {device.host_id || '—'}
            </p>
            <h2 className="truncate font-mono text-base font-semibold text-gray-900">
              {device.device_serial || `Device #${device.device_id}`}
            </h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-testid="device-drawer-close"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto px-4 py-3 text-sm">
          <div
            data-testid="device-drawer-status-pill"
            className={`mb-3 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${cfg.cls}`}
          >
            <Icon className={`h-3 w-3 ${device.ui_status === 'running' ? 'animate-spin' : ''}`} />
            {cfg.label} · {device.current_stage.toUpperCase()}
          </div>

          <KvList
            rows={[
              ['当前步骤', device.current_step || '—', true],
              ['Job 状态', device.job_status, false],
              ...(device.status_reason
                ? [[
                    '状态原因',
                    device.status_reason,
                    false,
                    device.ui_status === 'failed'
                      ? 'text-red-600 font-semibold'
                      : 'text-amber-700 font-semibold',
                  ] as [string, string, boolean, string]]
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
            <div className="mt-4 rounded-lg border-l-4 border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <div className="font-semibold">检测到 {device.log_signal_count} 条 Watcher 异常</div>
              <p className="mt-0.5 text-[11px] text-amber-700">
                明细见上方"业务流时间线"事件流(stage = patrol, severity = 异常)
              </p>
            </div>
          )}

          {(retryRequested || exitRequested) && (
            <div className="mt-4 rounded-lg border-l-4 border-blue-400 bg-blue-50 px-3 py-2 text-xs text-blue-800">
              <div className="font-semibold">
                {retryRequested ? '已请求立即重试' : '已请求退出该设备'}
              </div>
              <p className="mt-0.5 text-[11px] text-blue-700">
                Agent 将在下一次心跳处理(通常 10s 内)
              </p>
            </div>
          )}
        </div>

        {/* Footer — actions */}
        <footer className="grid grid-cols-2 gap-2 border-t bg-gray-50 px-4 py-3">
          <Button
            variant="outline"
            size="sm"
            data-testid="device-drawer-retry-btn"
            disabled={isTerminal || isRetryPending || retryRequested}
            onClick={() => setConfirmOpen('retry')}
          >
            {isRetryPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCw className="mr-1 h-3.5 w-3.5" />
            )}
            立即重试
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-testid="device-drawer-exit-btn"
            disabled={isTerminal || isExitPending || exitRequested}
            onClick={() => setConfirmOpen('exit')}
            className="border-red-200 text-red-700 hover:bg-red-50"
          >
            {isExitPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <LogOut className="mr-1 h-3.5 w-3.5" />
            )}
            退出该设备
          </Button>
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
                confirmOpen === 'exit' ? 'bg-red-600 text-white hover:bg-red-700' : ''
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

function KvList({ rows }: { rows: Array<[string, string, boolean, string?]> }) {
  return (
    <dl className="divide-y rounded-lg border">
      {rows.map(([k, v, mono, extraCls]) => (
        <div key={k} className="flex items-start justify-between px-3 py-1.5 text-xs">
          <dt className={`text-gray-500 ${extraCls || ''}`}>{k}</dt>
          <dd
            className={`max-w-[60%] text-right text-gray-900 ${
              mono ? 'font-mono' : ''
            } ${extraCls || ''}`}
          >
            {v}
          </dd>
        </div>
      ))}
    </dl>
  );
}
