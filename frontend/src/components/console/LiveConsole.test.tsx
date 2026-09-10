/**
 * #1116 — LiveConsole reconnect / gap replay.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react';

const written: string[][] = [];
const getRunLog = vi.fn();

type Status = 'connecting' | 'connected' | 'disconnected' | 'error';
let mockStatus: Status = 'disconnected';
let socketHandler: ((msg: unknown) => void) | null = null;
const statusListeners = new Set<(s: Status) => void>();

function setMockConnectionStatus(next: Status) {
  mockStatus = next;
  statusListeners.forEach((fn) => fn(next));
}

vi.mock('@/components/log/XTerminal', () => {
  const XTerminal = forwardRef(function MockXTerminal(
    { onReady }: { onReady?: () => void },
    ref,
  ) {
    useImperativeHandle(ref, () => ({
      writeLines: (rows: Array<{ msg: string }>) => {
        written.push(rows.map((r) => r.msg));
      },
      clear: () => {
        written.length = 0;
      },
    }));
    useEffect(() => {
      onReady?.();
    }, [onReady]);
    return <div data-testid="xterm-stub" />;
  });
  return { XTerminal };
});

vi.mock('@/utils/api/dedup', () => ({
  dedup: {
    getRunLog: (...args: unknown[]) => getRunLog(...args),
  },
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: (_url: string, opts: { onMessage?: (msg: unknown) => void }) => {
    socketHandler = opts.onMessage ?? null;
    const [connectionStatus, setStatus] = useState<Status>(mockStatus);
    useEffect(() => {
      const fn = (s: Status) => setStatus(s);
      statusListeners.add(fn);
      setStatus(mockStatus);
      return () => {
        statusListeners.delete(fn);
      };
    }, []);
    return { connectionStatus, isConnected: connectionStatus === 'connected' };
  },
}));

vi.mock('@/config', () => ({
  consoleSubscription: (id: string) => `console:${id}`,
}));

describe('LiveConsole reconnect gap fill (#1116)', () => {
  beforeEach(() => {
    written.length = 0;
    getRunLog.mockReset();
    socketHandler = null;
    mockStatus = 'disconnected';
    statusListeners.clear();
    vi.resetModules();
  });

  it('refills missed lines after gap + reconnect when run finished offline', async () => {
    getRunLog.mockImplementation(async (_id: string, fromSeq = 0) => {
      if (fromSeq === 0) {
        return {
          run_id: 'con-1',
          from_seq: 1,
          lines: ['line-1'],
          seq: 1,
          status: 'RUNNING',
        };
      }
      return {
        run_id: 'con-1',
        from_seq: 2,
        lines: ['line-2', 'line-3'],
        seq: 3,
        status: 'SUCCESS',
      };
    });

    const { default: LiveConsole } = await import('./LiveConsole');
    const { getByTestId } = render(<LiveConsole consoleRunId="con-1" />);

    await waitFor(() => {
      expect(getRunLog).toHaveBeenCalledWith('con-1', 0);
    });
    await waitFor(() => {
      expect(written.flat()).toEqual(['line-1']);
    });

    act(() => setMockConnectionStatus('connected'));
    act(() => setMockConnectionStatus('disconnected'));

    act(() => {
      socketHandler?.({
        run_id: 'con-1',
        from_seq: 3,
        lines: ['line-3'],
      });
    });

    await waitFor(() => {
      expect(getRunLog).toHaveBeenCalledWith('con-1', 2);
    });
    await waitFor(() => {
      expect(written.flat()).toEqual(['line-1', 'line-2', 'line-3']);
    });
    await waitFor(() => {
      expect(getByTestId('live-console-status')).toHaveTextContent('SUCCESS');
    });

    getRunLog.mockClear();
    getRunLog.mockResolvedValue({
      run_id: 'con-1',
      from_seq: 4,
      lines: [],
      seq: 3,
      status: 'SUCCESS',
    });
    act(() => setMockConnectionStatus('connected'));
    await waitFor(() => {
      expect(getRunLog).toHaveBeenCalledWith('con-1', 4);
    });
    expect(written.flat()).toEqual(['line-1', 'line-2', 'line-3']);
  });

  it('skips overlapping live lines after a contiguous batch', async () => {
    getRunLog.mockResolvedValue({
      run_id: 'con-2',
      from_seq: 1,
      lines: [],
      seq: 0,
      status: 'RUNNING',
    });

    const { default: LiveConsole } = await import('./LiveConsole');
    render(<LiveConsole consoleRunId="con-2" />);
    await waitFor(() => expect(getRunLog).toHaveBeenCalled());

    act(() => {
      setMockConnectionStatus('connected');
      socketHandler?.({
        run_id: 'con-2',
        from_seq: 1,
        lines: ['a', 'b'],
      });
    });
    await waitFor(() => {
      expect(written.flat()).toEqual(['a', 'b']);
    });

    act(() => {
      socketHandler?.({
        run_id: 'con-2',
        from_seq: 2,
        lines: ['b', 'c'],
      });
    });
    await waitFor(() => {
      expect(written.flat()).toEqual(['a', 'b', 'c']);
    });
  });

  it('refills a gap batch that arrives while a fill is already in flight (#1278)', async () => {
    let resolveFirstGap: ((v: unknown) => void) | null = null;
    getRunLog.mockImplementation((_id: string, fromSeq = 0) => {
      if (fromSeq === 0) {
        return Promise.resolve({
          run_id: 'con-3', from_seq: 1, lines: ['line-1'], seq: 1, status: 'RUNNING',
        });
      }
      if (fromSeq === 2) {
        return new Promise((resolve) => {
          resolveFirstGap = resolve;
        });
      }
      return Promise.resolve({
        run_id: 'con-3', from_seq: 3, lines: ['line-3'], seq: 3, status: 'SUCCESS',
      });
    });

    const { default: LiveConsole } = await import('./LiveConsole');
    render(<LiveConsole consoleRunId="con-3" />);
    await waitFor(() => expect(written.flat()).toEqual(['line-1']));

    // 第一个缺口批次 → fillGap 发出请求并悬挂在途
    act(() => {
      socketHandler?.({ run_id: 'con-3', from_seq: 3, lines: ['line-3'] });
    });
    await waitFor(() => expect(getRunLog).toHaveBeenCalledWith('con-3', 2));

    // in-flight 期间又来一个带缺口批次：必须记为「待补」，而非丢弃
    act(() => {
      socketHandler?.({ run_id: 'con-3', from_seq: 3, lines: ['line-3'] });
    });

    await act(async () => {
      resolveFirstGap?.({
        run_id: 'con-3', from_seq: 2, lines: ['line-2'], seq: 2, status: 'RUNNING',
      });
    });

    await waitFor(() => expect(getRunLog).toHaveBeenCalledWith('con-3', 3));
    await waitFor(() => expect(written.flat()).toEqual(['line-1', 'line-2', 'line-3']));
  });

  it('discards a stale gap-fill response after the run switches (#1278)', async () => {
    let resolveStaleGap: ((v: unknown) => void) | null = null;
    getRunLog.mockImplementation((id: string, fromSeq = 0) => {
      if (id === 'con-A') {
        if (fromSeq === 0) {
          return Promise.resolve({
            run_id: 'con-A', from_seq: 1, lines: ['A-1'], seq: 1, status: 'RUNNING',
          });
        }
        return new Promise((resolve) => {
          resolveStaleGap = resolve;
        });
      }
      return Promise.resolve({
        run_id: 'con-B', from_seq: 1, lines: ['B-1'], seq: 1, status: 'RUNNING',
      });
    });

    const { default: LiveConsole } = await import('./LiveConsole');
    const { rerender, getByTestId } = render(<LiveConsole consoleRunId="con-A" />);
    await waitFor(() => expect(written.flat()).toEqual(['A-1']));

    act(() => {
      socketHandler?.({ run_id: 'con-A', from_seq: 3, lines: ['A-3'] });
    });
    await waitFor(() => expect(getRunLog).toHaveBeenCalledWith('con-A', 2));

    // 切换 run：旧请求仍在途
    rerender(<LiveConsole consoleRunId="con-B" />);
    await waitFor(() => expect(getRunLog).toHaveBeenCalledWith('con-B', 0));
    await waitFor(() => expect(written.flat()).toEqual(['B-1']));

    await act(async () => {
      resolveStaleGap?.({
        run_id: 'con-A', from_seq: 2, lines: ['stale-A-2'], seq: 2, status: 'SUCCESS',
      });
    });
    // 迟到结果不得写入新 run 终端，也不得推进其状态（旧实现会把 SUCCESS 应用到 con-B）
    expect(written.flat()).toEqual(['B-1']);
    expect(getByTestId('live-console-status')).toHaveTextContent('RUNNING');
  });
});
