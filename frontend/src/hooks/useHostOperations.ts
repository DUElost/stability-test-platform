/**
 * 主机运维操作编排：单台/批量安装共用，前端并发闸门（默认 2）。
 * 热更新批量不在此 hook（须先 SAQ 化）。
 */
import { useCallback, useRef, useState } from 'react';
import { api } from '@/utils/api';

export type HostOpKind = 'install' | 'reinstall';
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
}

const DEFAULT_CONCURRENCY = 2;

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

export function useHostOperations(opts?: { concurrency?: number }) {
  const concurrency = opts?.concurrency ?? DEFAULT_CONCURRENCY;
  const [ops, setOps] = useState<HostOpItem[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const runningRef = useRef(false);

  const updateOp = useCallback((hostId: string, patch: Partial<HostOpItem>) => {
    setOps((prev) =>
      prev.map((op) => (op.hostId === hostId ? { ...op, ...patch } : op)),
    );
  }, []);

  const startInstallBatch = useCallback(
    async (targets: HostOpTarget[]) => {
      if (!targets.length || runningRef.current) return;
      runningRef.current = true;

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
          try {
            const res = await api.agentInstall.trigger(item.hostId);
            updateOp(item.hostId, {
              status: 'running',
              consoleRunId: res.console_run_id,
            });
          } catch (err) {
            const cid = extract409ConsoleId(err);
            if (cid) {
              updateOp(item.hostId, {
                status: 'running',
                consoleRunId: cid,
              });
              return;
            }
            updateOp(item.hostId, {
              status: 'failed',
              error: extractErrorMessage(err),
            });
          }
        });
      } finally {
        runningRef.current = false;
      }
    },
    [concurrency, updateOp],
  );

  const markTerminal = useCallback(
    (hostId: string, status: 'success' | 'failed' | 'skipped', error?: string) => {
      updateOp(hostId, { status, error });
    },
    [updateOp],
  );

  const closePanel = useCallback(() => {
    setPanelOpen(false);
  }, []);

  const clearOps = useCallback(() => {
    setOps([]);
    setPanelOpen(false);
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

  return {
    ops,
    panelOpen,
    setPanelOpen,
    startInstallBatch,
    markTerminal,
    closePanel,
    clearOps,
    isHostBusy,
  };
}
