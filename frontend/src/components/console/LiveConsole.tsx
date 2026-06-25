/**
 * LiveConsole — ADR-0025 §9 RunConsole 的前端控制台（Jenkins 式 web 实时日志）。
 */
import { useEffect, useRef, useState } from 'react';
import { XTerminal, type XTerminalHandle } from '@/components/log/XTerminal';
import { useSocketIO } from '@/hooks/useSocketIO';
import { dedup } from '@/utils/api/dedup';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface Props {
  consoleRunId: string;
  height?: string;
  onStatusChange?: (status: string) => void;
}

const STATUS_TONE: Record<string, string> = {
  RUNNING: STATUS_BG_COLORS.primary,
  SUCCESS: STATUS_BG_COLORS.success,
  FAILED: STATUS_BG_COLORS.error,
  CANCELED: STATUS_BG_COLORS.muted,
};

export default function LiveConsole({ consoleRunId, height = '420px', onStatusChange }: Props) {
  const termRef = useRef<XTerminalHandle>(null);
  const seqRef = useRef(0);
  const [status, setStatus] = useState('RUNNING');

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
    <div className={cn('overflow-hidden rounded-lg', PANEL.root)} data-testid="live-console">
      <div className={cn('flex items-center justify-between border-b px-3 py-1.5', PANEL.footer)}>
        <span className={cn('font-mono text-[11px]', TEXT.subtitle)}>{consoleRunId}</span>
        <span
          data-testid="live-console-status"
          className={cn(
            'rounded px-2 py-0.5 text-[11px] font-semibold',
            STATUS_TONE[status] ?? 'bg-muted text-muted-foreground',
          )}
        >
          {status}
        </span>
      </div>
      <XTerminal ref={termRef} poolKey={`console-${consoleRunId}`} height={height} />
    </div>
  );
}
