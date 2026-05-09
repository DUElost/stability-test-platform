import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Loader2,
  ShieldCheck,
  RotateCw,
  Server,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { api } from '@/utils/api';
import type { HostActiveJob } from '@/utils/api/types';

interface Props {
  /** When set, the dialog is open and will fetch live `active_jobs`. */
  hostId: number | string | null;
  onClose: () => void;
  /** Called when user confirms; `abortRunningJobs` is true iff the user
   *  explicitly opted into the abort-then-update path. */
  onConfirm: (
    hostId: number | string,
    opts: { abortRunningJobs: boolean },
  ) => void;
  isHotUpdatePending?: boolean;
}

function fmtTime(ts?: string | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

function ActiveJobRow({ job }: { job: HostActiveJob }) {
  return (
    <div
      data-testid={`hot-update-active-job-${job.id}`}
      className="grid grid-cols-[60px_1fr_auto_auto] items-center gap-2 border-b px-3 py-1.5 text-xs last:border-b-0"
    >
      <span className="font-mono text-[11px] text-gray-500">#{job.id}</span>
      <span className="truncate font-mono text-[11px] text-gray-700">
        Device #{job.device_id}
        {job.plan_run_id && (
          <span className="ml-2 text-gray-400">
            · PlanRun #{job.plan_run_id}
          </span>
        )}
      </span>
      <span
        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
          job.status === 'RUNNING'
            ? 'bg-orange-100 text-orange-800'
            : 'bg-blue-100 text-blue-800'
        }`}
      >
        {job.status}
      </span>
      {job.abort_pending ? (
        <span
          data-testid={`hot-update-job-abort-pending-${job.id}`}
          className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700"
        >
          收口中…
        </span>
      ) : (
        <span className="font-mono text-[10.5px] text-gray-400">
          {fmtTime(job.started_at)}
        </span>
      )}
    </div>
  );
}

export default function HostHotUpdateConfirmDialog({
  hostId,
  onClose,
  onConfirm,
  isHotUpdatePending = false,
}: Props) {
  const [abortChecked, setAbortChecked] = useState(false);

  // Always fetch the live host snapshot when the dialog opens — the host list
  // payload doesn't include the per-host `active_jobs` array, so we must hit
  // GET /hosts/{id}.  We disable caching on this query so re-opening the
  // dialog always reflects the latest state.
  const detailQ = useQuery({
    queryKey: ['host-detail', hostId],
    queryFn: () => api.hosts.getDetail(hostId!),
    enabled: hostId != null,
    staleTime: 0,
    refetchOnMount: 'always',
  });

  // Reset the abort opt-in every time the dialog opens for a different host.
  useEffect(() => {
    setAbortChecked(false);
  }, [hostId]);

  const open = hostId != null;
  const detail = detailQ.data;
  const activeCount = detail?.active_job_count ?? 0;
  const hasActive = activeCount > 0;

  // v3: abort 收口中 — 所有 active job 均已标记 abort_requested (abort reaper 待收割).
  const allAbortPending = hasActive && (detail?.active_jobs ?? []).every((j) => j.abort_pending);

  // Confirm rules:
  //   - loading host detail        → disabled
  //   - hot-update mutation flying → disabled
  //   - all-abort-pending          → disabled (abort 收口中,等待 reaper 收割)
  //   - active=0                   → enabled (direct hot-update)
  //   - active>0 + NOT opted in    → disabled (forces user to acknowledge)
  //   - active>0 + opted in        → enabled (abort_running_jobs=true)
  const confirmDisabled =
    detailQ.isLoading ||
    isHotUpdatePending ||
    allAbortPending ||
    (hasActive && !abortChecked);

  const handleConfirm = () => {
    if (!hostId) return;
    onConfirm(hostId, { abortRunningJobs: hasActive && abortChecked });
  };

  return (
    <AlertDialog open={open} onOpenChange={(o) => !o && onClose()}>
      <AlertDialogContent
        data-testid="host-hot-update-dialog"
        className="max-w-lg"
      >
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <RotateCw className="h-4 w-4" />
            热更新主机 {hostId != null ? `#${hostId}` : ''}
          </AlertDialogTitle>
          <AlertDialogDescription>
            将同步最新代码到 Agent 并重启服务。重启会中断该主机当前所有 Agent 进程。
          </AlertDialogDescription>
        </AlertDialogHeader>

        {/* Active-jobs section */}
        <section className="space-y-2 rounded-lg border bg-gray-50 p-3">
          {detailQ.isLoading ? (
            <div
              data-testid="host-detail-loading"
              className="flex items-center justify-center py-4 text-xs text-gray-500"
            >
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> 拉取活跃 Job…
            </div>
          ) : detailQ.isError ? (
            <div className="flex items-center gap-2 rounded border-l-4 border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {(detailQ.error as Error)?.message || '拉取主机失败'}
            </div>
          ) : !hasActive ? (
            <div
              data-testid="host-no-active-jobs"
              className="flex items-center gap-2 rounded border-l-4 border-green-300 bg-green-50 px-3 py-2 text-xs text-green-800"
            >
              <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
              该主机当前无活跃 Job,可直接执行热更新
            </div>
          ) : allAbortPending ? (
            <>
              <div
                data-testid="host-abort-draining-banner"
                className="flex items-center gap-2 rounded border-l-4 border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-900"
              >
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                <span>
                  Abort 收口中 —{' '}
                  <b
                    data-testid="host-active-job-count"
                    className="font-mono"
                  >
                    {activeCount}
                  </b>{' '}
                  个 Job 正在退出,请稍后重试
                </span>
              </div>

              <div className="rounded-md border bg-white">
                <div className="flex items-center gap-2 border-b bg-gray-50 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                  <Server className="h-3 w-3" /> 收口中的 Job
                </div>
                <div className="max-h-40 overflow-y-auto">
                  {detail?.active_jobs?.map((j) => (
                    <ActiveJobRow key={j.id} job={j} />
                  ))}
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 rounded border-l-4 border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span>
                  该主机仍有{' '}
                  <b
                    data-testid="host-active-job-count"
                    className="font-mono"
                  >
                    {activeCount}
                  </b>{' '}
                  个活跃 Job — 默认拒绝热更新(避免破坏运行中的测试)
                </span>
              </div>

              <div className="rounded-md border bg-white">
                <div className="flex items-center gap-2 border-b bg-gray-50 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                  <Server className="h-3 w-3" /> 受影响 Job
                </div>
                <div className="max-h-40 overflow-y-auto">
                  {detail?.active_jobs?.map((j) => (
                    <ActiveJobRow key={j.id} job={j} />
                  ))}
                </div>
              </div>

              <label
                data-testid="host-hot-update-abort-toggle-label"
                className="flex cursor-pointer items-start gap-2 rounded border-l-4 border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800"
              >
                <input
                  type="checkbox"
                  data-testid="host-hot-update-abort-toggle"
                  checked={abortChecked}
                  onChange={(e) => setAbortChecked(e.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5 cursor-pointer accent-red-600"
                />
                <span>
                  我已知悉:确认后将先 abort 上述 {activeCount} 个 Job
                  (释放设备租约、Agent 自然退出 ≤45s),然后执行热更新。
                  <span className="block text-[10.5px] text-red-700">
                    所有受影响的 PlanRun 会被标记为
                    <b className="font-mono">FAILED</b> /{' '}
                    <b className="font-mono">DEGRADED</b>。
                  </span>
                </span>
              </label>
            </>
          )}
        </section>

        <AlertDialogFooter>
          <Button
            variant="outline"
            data-testid="host-hot-update-cancel"
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            data-testid="host-hot-update-confirm"
            disabled={confirmDisabled}
            onClick={handleConfirm}
            className={
              hasActive && abortChecked
                ? 'bg-red-600 text-white hover:bg-red-700'
                : ''
            }
          >
            {isHotUpdatePending && (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            )}
            {allAbortPending
              ? 'Abort 收口中…'
              : hasActive && abortChecked
              ? '中止 Job 并热更新'
              : hasActive
              ? '需先勾选确认'
              : '执行热更新'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
