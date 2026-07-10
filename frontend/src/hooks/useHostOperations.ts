/**
 * 主机运维操作编排：单台/批量安装共用，前端并发闸门（默认 2）。
 * 闸门语义：同时最多 N 台 ansible 在跑（trigger 后轮询至终态才释放槽位）。
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

export interface HostOpTerminalEvent {
  hostId: string;
  label: string;
  ok: boolean;
  status: string;
  error?: string;
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
