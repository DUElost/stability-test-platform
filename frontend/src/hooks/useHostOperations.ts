/**
 * 主机运维操作编排：单台/批量安装与热更新共用，前端并发闸门（默认 2）。
 * 闸门语义：同时最多 N 台在跑（安装：trigger 后轮询至终态才释放槽位；
 * 热更新：同步 SSH，请求返回即终态，暂无 RunConsole 实时日志）。
 */
import { useCallback, useRef, useState } from 'react';
import { api } from '@/utils/api';
import type { HotUpdateResult } from '@/utils/api/hosts';

export type HostOpKind = 'install' | 'reinstall' | 'hot_update';
export type HostOpStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'skipped';

export interface HostOpItem {
  hostId: string;
  label: string;
  kind: HostOpKind;
  status: HostOpStatus;
  consoleRunId?: string | null;
  error?: string;
}

export interface HostOpTarget {
  hostId: string | number;
  label: string;
  agentInstalled?: boolean;
  abortRunningJobs?: boolean;
}

export interface HostOpTerminalEvent {
  hostId: string;
  label: string;
  kind: HostOpKind;
  ok: boolean;
  status: string;
  error?: string;
}

export interface HotUpdateSkippedSeed {
  hostId: string | number;
  label: string;
  error: string;
}

export interface HotUpdateBatchResult {
  succeeded: Array<{ hostId: string; label: string; result: HotUpdateResult }>;
  failed: Array<{ hostId: string; label: string; error: string; httpStatus?: number }>;
  skipped: Array<{
    hostId: string;
    label: string;
    error: string;
    httpStatus?: number;
    retryAfterSeconds?: number;
    activeJobCount?: number;
  }>;
}

const DEFAULT_CONCURRENCY = 2;
const DEFAULT_POLL_MS = 2000;
const DEFAULT_TIMEOUT_MS = 900_000;

function extractErrorMessage(err: unknown): string {
  const ax = err as {
    response?: { status?: number; data?: { detail?: unknown } };
    message?: string;
  };
  const detail = ax?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message?: string }).message);
  }
  return ax?.message ?? '未知错误';
}

function extractHttpStatus(err: unknown): number | undefined {
  const ax = err as { response?: { status?: number } };
  return typeof ax?.response?.status === 'number' ? ax.response.status : undefined;
}

function extractHotUpdateConflict(err: unknown): {
  message: string;
  retryAfterSeconds?: number;
  activeJobCount?: number;
} | null {
  if (extractHttpStatus(err) !== 409) return null;
  const ax = err as { response?: { data?: { detail?: unknown } } };
  const detail = ax?.response?.data?.detail;
  if (detail && typeof detail === 'object' && detail !== null) {
    const d = detail as {
      message?: string;
      retry_after_seconds?: number;
      active_jobs?: unknown[];
    };
    return {
      message: typeof d.message === 'string' ? d.message : '主机状态冲突',
      retryAfterSeconds:
        typeof d.retry_after_seconds === 'number' ? d.retry_after_seconds : undefined,
      activeJobCount: Array.isArray(d.active_jobs) ? d.active_jobs.length : undefined,
    };
  }
  return { message: '主机状态冲突' };
}

function extract409ConsoleId(err: unknown): string | null {
  const ax = err as { response?: { status?: number; data?: { detail?: unknown } } };
  if (ax?.response?.status !== 409) return null;
  const detail = ax.response.data?.detail;
  if (
    detail &&
    typeof detail === 'object' &&
    detail !== null &&
    'console_run_id' in detail &&
    typeof (detail as { console_run_id?: string }).console_run_id === 'string'
  ) {
    return (detail as { console_run_id: string }).console_run_id;
  }
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 轮询至 RunConsole / SAQ 终态；槽位占用直到返回。 */
export async function waitInstallTerminal(
  hostId: string,
  opts: { pollMs?: number; timeoutMs?: number } = {},
): Promise<{ ok: boolean; status: string; message?: string }> {
  const pollMs = opts.pollMs ?? DEFAULT_POLL_MS;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    try {
      const st = await api.agentInstall.status(hostId);
      const cs = st.console_status;
      if (cs === 'SUCCESS') return { ok: true, status: cs };
      if (cs === 'FAILED' || cs === 'CANCELED') {
        return {
          ok: false,
          status: cs,
          message: st.result?.message ?? cs,
        };
      }
      if (st.status === 'complete') {
        const ok = Boolean(st.result?.ok);
        return {
          ok,
          status: ok ? 'SUCCESS' : 'FAILED',
          message: st.result?.message,
        };
      }
      if (st.status === 'failed' || st.status === 'aborted') {
        return {
          ok: false,
          status: st.status.toUpperCase(),
          message: st.result?.message ?? st.status,
        };
      }
    } catch {
      /* 短暂失败继续轮询 */
    }
    await sleep(pollMs);
  }
  return { ok: false, status: 'TIMEOUT', message: `等待安装超时（${Math.round(timeoutMs / 1000)}s）` };
}

async function mapPool<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return results;
}

export function useHostOperations(opts?: {
  concurrency?: number;
  pollMs?: number;
  onTerminal?: (ev: HostOpTerminalEvent) => void;
}) {
  const concurrency = opts?.concurrency ?? DEFAULT_CONCURRENCY;
  const pollMs = opts?.pollMs ?? DEFAULT_POLL_MS;
  const onTerminalRef = useRef(opts?.onTerminal);
  onTerminalRef.current = opts?.onTerminal;

  const [ops, setOps] = useState<HostOpItem[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const runningRef = useRef(false);
  const terminalNotifiedRef = useRef<Set<string>>(new Set());

  const updateOp = useCallback((hostId: string, patch: Partial<HostOpItem>) => {
    setOps((prev) =>
      prev.map((op) => (op.hostId === hostId ? { ...op, ...patch } : op)),
    );
  }, []);

  const emitTerminal = useCallback(
    (item: HostOpItem, ok: boolean, status: string, error?: string) => {
      const key = `${item.hostId}:${status}`;
      if (terminalNotifiedRef.current.has(key)) return;
      terminalNotifiedRef.current.add(key);
      onTerminalRef.current?.({
        hostId: item.hostId,
        label: item.label,
        kind: item.kind,
        ok,
        status,
        error,
      });
    },
    [],
  );

  const opsRef = useRef(ops);
  opsRef.current = ops;

  const markTerminal = useCallback(
    (hostId: string, status: 'success' | 'failed' | 'skipped', error?: string) => {
      const prev = opsRef.current.find((o) => o.hostId === hostId);
      if (prev && (prev.status === 'success' || prev.status === 'failed')) {
        return;
      }
      updateOp(hostId, { status, error });
      const item = prev ?? {
        hostId,
        label: hostId,
        kind: 'install' as const,
        status,
      };
      emitTerminal(
        { ...item, status },
        status === 'success',
        status === 'success' ? 'SUCCESS' : 'FAILED',
        error,
      );
    },
    [emitTerminal, updateOp],
  );

  const startInstallBatch = useCallback(
    async (targets: HostOpTarget[]) => {
      if (!targets.length || runningRef.current) return;
      runningRef.current = true;
      terminalNotifiedRef.current = new Set();

      const initial: HostOpItem[] = targets.map((t) => ({
        hostId: String(t.hostId),
        label: t.label,
        kind: t.agentInstalled ? 'reinstall' : 'install',
        status: 'pending',
        consoleRunId: null,
      }));
      setOps(initial);
      setPanelOpen(true);

      try {
        await mapPool(initial, concurrency, async (item) => {
          updateOp(item.hostId, { status: 'running' });
          let consoleRunId: string | null = null;
          try {
            const res = await api.agentInstall.trigger(item.hostId);
            consoleRunId = res.console_run_id;
            updateOp(item.hostId, {
              status: 'running',
              consoleRunId,
            });
          } catch (err) {
            const cid = extract409ConsoleId(err);
            if (cid) {
              consoleRunId = cid;
              updateOp(item.hostId, {
                status: 'running',
                consoleRunId: cid,
              });
            } else {
              const message = extractErrorMessage(err);
              updateOp(item.hostId, { status: 'failed', error: message });
              emitTerminal(item, false, 'FAILED', message);
              return;
            }
          }

          // 占用并发槽直至该主机安装终态（真正限制同时跑的 ansible 数）
          const terminal = await waitInstallTerminal(item.hostId, { pollMs });
          if (terminal.ok) {
            updateOp(item.hostId, {
              status: 'success',
              consoleRunId: consoleRunId,
            });
            emitTerminal(item, true, terminal.status);
          } else {
            updateOp(item.hostId, {
              status: 'failed',
              consoleRunId: consoleRunId,
              error: terminal.message ?? terminal.status,
            });
            emitTerminal(item, false, terminal.status, terminal.message);
          }
        });
      } finally {
        runningRef.current = false;
      }
    },
    [concurrency, emitTerminal, pollMs, updateOp],
  );

  const startHotUpdateBatch = useCallback(
    async (
      targets: HostOpTarget[],
      opts?: {
        skipped?: HotUpdateSkippedSeed[];
        onProgress?: (completed: number, total: number) => void;
      },
    ): Promise<HotUpdateBatchResult | null> => {
      if (runningRef.current) return null;
      if (!targets.length && !opts?.skipped?.length) {
        return { succeeded: [], failed: [], skipped: [] };
      }
      runningRef.current = true;
      terminalNotifiedRef.current = new Set();

      const skippedSeed: HostOpItem[] = (opts?.skipped ?? []).map((s) => ({
        hostId: String(s.hostId),
        label: s.label,
        kind: 'hot_update',
        status: 'skipped',
        error: s.error,
      }));
      const initial: HostOpItem[] = [
        ...skippedSeed,
        ...targets.map((t) => ({
          hostId: String(t.hostId),
          label: t.label,
          kind: 'hot_update' as const,
          status: 'pending' as const,
        })),
      ];
      setOps(initial);
      setPanelOpen(true);

      const succeeded: HotUpdateBatchResult['succeeded'] = [];
      const failed: HotUpdateBatchResult['failed'] = [];
      const skipped: HotUpdateBatchResult['skipped'] = skippedSeed.map((s) => ({
        hostId: s.hostId,
        label: s.label,
        error: s.error ?? '',
      }));
      let completed = 0;

      try {
        if (targets.length === 0) return { succeeded, failed, skipped };

        await mapPool(targets, concurrency, async (target) => {
          const hostId = String(target.hostId);
          const item: HostOpItem = {
            hostId,
            label: target.label,
            kind: 'hot_update',
            status: 'running',
          };
          updateOp(hostId, { status: 'running' });
          try {
            const result = await api.hotUpdate.trigger(target.hostId, {
              abortRunningJobs: Boolean(target.abortRunningJobs),
            });
            updateOp(hostId, { status: 'success' });
            succeeded.push({ hostId, label: target.label, result });
            emitTerminal({ ...item, status: 'success' }, true, 'SUCCESS');
          } catch (err) {
            const conflict = extractHotUpdateConflict(err);
            if (conflict) {
              updateOp(hostId, { status: 'skipped', error: conflict.message });
              skipped.push({
                hostId,
                label: target.label,
                error: conflict.message,
                httpStatus: 409,
                retryAfterSeconds: conflict.retryAfterSeconds,
                activeJobCount: conflict.activeJobCount,
              });
              emitTerminal({ ...item, status: 'skipped' }, false, 'SKIPPED', conflict.message);
            } else {
              const message = extractErrorMessage(err);
              updateOp(hostId, { status: 'failed', error: message });
              failed.push({
                hostId,
                label: target.label,
                error: message,
                httpStatus: extractHttpStatus(err),
              });
              emitTerminal({ ...item, status: 'failed' }, false, 'FAILED', message);
            }
          } finally {
            completed += 1;
            opts?.onProgress?.(completed, targets.length);
          }
        });
      } finally {
        runningRef.current = false;
      }

      return { succeeded, failed, skipped };
    },
    [concurrency, emitTerminal, updateOp],
  );

  const closePanel = useCallback(() => {
    setPanelOpen(false);
  }, []);

  const clearOps = useCallback(() => {
    setOps([]);
    setPanelOpen(false);
    terminalNotifiedRef.current = new Set();
  }, []);

  const isHostBusy = useCallback(
    (hostId: string | number) => {
      const id = String(hostId);
      return ops.some(
        (op) => op.hostId === id && (op.status === 'pending' || op.status === 'running'),
      );
    },
    [ops],
  );

  const isHostOpBusy = useCallback(
    (hostId: string | number, kind: HostOpKind | HostOpKind[]) => {
      const id = String(hostId);
      const kinds = Array.isArray(kind) ? kind : [kind];
      return ops.some(
        (op) =>
          op.hostId === id &&
          kinds.includes(op.kind) &&
          (op.status === 'pending' || op.status === 'running'),
      );
    },
    [ops],
  );

  return {
    ops,
    panelOpen,
    setPanelOpen,
    startInstallBatch,
    startHotUpdateBatch,
    markTerminal,
    closePanel,
    clearOps,
    isHostBusy,
    isHostOpBusy,
  };
}
