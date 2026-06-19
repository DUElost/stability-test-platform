/**
 * LiveConsole — ADR-0025 §9 RunConsole 的前端控制台（Jenkins 式 web 实时日志）。
 *
 * 复用 XTerminal(xterm.js) + useSocketIO：挂载时 GET 回填历史，随后订阅
 * SocketIO room `console:{id}` 实时增量。事件按 payload 形态 + run_id 过滤
 * （console_log 带 lines / console_status 带 status）。
 */
import { useEffect, useRef, useState } from 'react';
import { XTerminal, type XTerminalHandle } from '@/components/log/XTerminal';
import { useSocketIO } from '@/hooks/useSocketIO';
import { dedup } from '@/utils/api/dedup';

interface Props {
  consoleRunId: string;
  height?: string;
  onStatusChange?: (status: string) => void;
}

const STATUS_TONE: Record<string, string> = {
  RUNNING: 'bg-blue-100 text-blue-700',
  SUCCESS: 'bg-green-100 text-green-700',
  FAILED: 'bg-red-100 text-red-700',
  CANCELED: 'bg-gray-200 text-gray-600',
};

export default function LiveConsole({ consoleRunId, height = '420px', onStatusChange }: Props) {
  const termRef = useRef<XTerminalHandle>(null);
  const seqRef = useRef(0);
  const [status, setStatus] = useState('RUNNING');

  // 挂载 / 切换 run → 清屏 + GET 回填
  useEffect(() => {
    let cancelled = false;
    seqRef.current = 0;
    termRef.current?.clear();
    dedup
      .getRunLog(consoleRunId, 0)
      .then((res) => {
        if (cancelled) return;
        if (res.lines.length) {
          termRef.current?.writeLines(res.lines.map((msg) => ({ msg })));
        }
        seqRef.current = res.seq;
        setStatus(res.status);
        onStatusChange?.(res.status);
      })
      .catch(() => {
        /* 回填失败不阻塞实时流 */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [consoleRunId]);

  // 实时增量
  useSocketIO(`/ws/console/${consoleRunId}`, {
    onMessage: (msg: unknown) => {
      const d = msg as { run_id?: string; from_seq?: number; lines?: string[]; status?: string };
      if (!d || d.run_id !== consoleRunId) return;
      if (Array.isArray(d.lines)) {
        const from = d.from_seq ?? seqRef.current + 1;
        if (from > seqRef.current) {
          termRef.current?.writeLines(d.lines.map((m) => ({ msg: m })));
          seqRef.current = from - 1 + d.lines.length;
        }
      } else if (typeof d.status === 'string') {
        setStatus(d.status);
        onStatusChange?.(d.status);
      }
    },
  });

  return (
    <div className="rounded-lg border border-gray-200" data-testid="live-console">
      <div className="flex items-center justify-between border-b bg-gray-50 px-3 py-1.5">
        <span className="font-mono text-[11px] text-gray-500">{consoleRunId}</span>
        <span
          data-testid="live-console-status"
          className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
            STATUS_TONE[status] ?? 'bg-gray-100 text-gray-600'
          }`}
        >
          {status}
        </span>
      </div>
      <XTerminal ref={termRef} poolKey={`console-${consoleRunId}`} height={height} />
    </div>
  );
}
