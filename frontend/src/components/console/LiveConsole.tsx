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
  /** 启用后状态条显示从日志解析到的 issue key 计数（Jira 建单场景） */
  enableIssueCount?: boolean;
}

const STATUS_TONE: Record<string, string> = {
  RUNNING: STATUS_BG_COLORS.primary,
  SUCCESS: STATUS_BG_COLORS.success,
  FAILED: STATUS_BG_COLORS.error,
  CANCELED: STATUS_BG_COLORS.muted,
};

const ISSUE_KEY_RE = /\b[A-Z][A-Z0-9_]{1,}-\d+\b/g;

function extractIssueKeys(lines: string[]): string[] {
  const out: string[] = [];
  for (const ln of lines) {
    if (!ln) continue;
    for (const m of ln.matchAll(ISSUE_KEY_RE)) {
      const k = m[0];
      if (!out.includes(k)) out.push(k);
    }
  }
  return out;
}

export default function LiveConsole({ consoleRunId, height = '420px', onStatusChange, enableIssueCount }: Props) {
  const termRef = useRef<XTerminalHandle>(null);
  const seqRef = useRef(0);
  const issueKeysRef = useRef<Set<string>>(new Set());
  const [status, setStatus] = useState('RUNNING');
  const [issueCount, setIssueCount] = useState(0);

  const tallyIssues = (lines: string[]) => {
    if (!enableIssueCount) return;
    for (const k of extractIssueKeys(lines)) {
      issueKeysRef.current.add(k);
    }
    setIssueCount(issueKeysRef.current.size);
  };

  useEffect(() => {
    let cancelled = false;
    seqRef.current = 0;
    issueKeysRef.current = new Set();
    setIssueCount(0);
    termRef.current?.clear();
    dedup
      .getRunLog(consoleRunId, 0)
      .then((res) => {
        if (cancelled) return;
        if (res.lines.length) {
          termRef.current?.writeLines(res.lines.map((msg) => ({ msg })));
          tallyIssues(res.lines);
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
          tallyIssues(d.lines);
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
        <div className="flex items-center gap-2">
          {enableIssueCount && issueCount > 0 && (
            <span
              data-testid="live-console-issue-count"
              className="rounded bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary"
            >
              {issueCount} issues
            </span>
          )}
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
      </div>
      <XTerminal ref={termRef} poolKey={`console-${consoleRunId}`} height={height} />
    </div>
  );
}
