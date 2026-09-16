/**
 * LiveConsole — ADR-0025 §9 RunConsole 的前端控制台（Jenkins 式 web 实时日志）。
 *
 * 注意：日志回放必须在 XTerminal onReady 之后执行，否则折叠再展开时
 * termRef 尚未就绪，writeLines 会被静默丢弃 → 空白终端。
 *
 * #1116：断线重连后按 seq 增量补齐；live 批次若跳号则触发 gap fill，
 * 并按行跳过与已写区间的重叠。
 *
 * #2070 / #2039：`seqRef` 只代表**已交付**到终端的行号。replay 响应的 `seq` 已是
 * 游标语义（不等于日志总长），未交付部分由 `truncated` 显式告知 → 按页续拉；
 * 跨实例读不到时由 `replay_unavailable` 告知 → 标「日志不完整」并退避，
 * 绝不把游标推过未交付行（那会让中间段与其后的实时行一并被丢弃）。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { XTerminal, type XTerminalHandle } from '@/components/log/XTerminal';
import { useSocketIO } from '@/hooks/useSocketIO';
import { consoleSubscription } from '@/config';
import { dedup } from '@/utils/api/dedup';
import { ALERT_BANNER, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface Props {
  consoleRunId: string;
  height?: string;
  onStatusChange?: (status: string) => void;
  /** 启用后状态条显示从日志解析到的 issue key 计数（Jira 建单场景） */
  enableIssueCount?: boolean;
}

const STATUS_TONE: Record<string, string> = {
  // RUNNING=warning：与 StatusBadge/矩阵瓦片的「运行中」琥珀统一（#356/#374）
  RUNNING: STATUS_CHIP.warning,
  SUCCESS: STATUS_CHIP.success,
  FAILED: STATUS_CHIP.destructive,
  CANCELED: STATUS_CHIP.muted,
};

const ISSUE_KEY_RE = /\b[A-Z][A-Z0-9_]{1,}-\d+\b/g;

/** #2070：一轮 gap fill 最多续拉多少页（一页 = 一次 replay 响应），防病态自旋。 */
const GAP_FILL_MAX_PAGES = 50;
/** #2039：回放不可用时的退避（ms）——不必每个 live 批次都打一次注定读不到的请求。 */
const REPLAY_UNAVAILABLE_BACKOFF_MS = 5000;

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
  const onStatusChangeRef = useRef(onStatusChange);
  const gapFillInFlightRef = useRef(false);
  const everConnectedRef = useRef(false);
  // #1278：in-flight 期间到达的「有缺口」批次只置位，由在途请求结束后再补一轮，
  // 否则该行段要等下一个 live 批次才可能被补——run 转终态/静默时即永久丢。
  const pendingGapRef = useRef(false);
  // #2039：replay 不可用（跨实例日志根未共享）时的退避截止时间戳
  const gapFillBlockedUntilRef = useRef(0);
  const consoleRunIdRef = useRef(consoleRunId);
  useLayoutEffect(() => {
    onStatusChangeRef.current = onStatusChange;
  }, [onStatusChange]);
  useLayoutEffect(() => {
    consoleRunIdRef.current = consoleRunId;
  }, [consoleRunId]);
  const [status, setStatus] = useState('RUNNING');
  const [replayIncomplete, setReplayIncomplete] = useState(false);
  const [issueCount, setIssueCount] = useState(0);
  const [termReady, setTermReady] = useState(false);
  const [prevConsoleRunId, setPrevConsoleRunId] = useState(consoleRunId);
  if (prevConsoleRunId !== consoleRunId) {
    setPrevConsoleRunId(consoleRunId);
    setTermReady(false);
  }

  // Reset reconnect/gap-fill guards off the render path (react-hooks/refs).
  useEffect(() => {
    everConnectedRef.current = false;
    gapFillInFlightRef.current = false;
    pendingGapRef.current = false;
    gapFillBlockedUntilRef.current = 0;
  }, [consoleRunId]);

  // memo 化是为了能进 replayFromStart 的依赖数组：裸函数每次渲染换引用，
  // 会让 replayFromStart 每帧重建，进而让 termReady effect 反复重跑回放。
  const tallyIssues = useCallback((lines: string[]) => {
    if (!enableIssueCount) return;
    for (const k of extractIssueKeys(lines)) {
      issueKeysRef.current.add(k);
    }
    setIssueCount(issueKeysRef.current.size);
  }, [enableIssueCount]);

  const applyLines = useCallback((from: number, lines: string[]) => {
    if (!lines.length) return;
    const expected = seqRef.current + 1;
    const batchEnd = from - 1 + lines.length;
    if (batchEnd <= seqRef.current) {
      // Fully overlapped with already-written history.
      return;
    }
    if (from > expected) {
      // Caller should have requested gap fill; still refuse to create a hole.
      return;
    }
    const skip = Math.max(0, expected - from);
    const newLines = lines.slice(skip);
    if (!newLines.length) return;
    termRef.current?.writeLines(newLines.map((msg) => ({ msg })));
    tallyIssues(newLines);
    seqRef.current = batchEnd;
  }, [tallyIssues]);

  /**
   * Incremental replay from the next missing seq (#1116)，并按页补齐被上限截断的
   * 尾部（#2070）；跨实例读不到时标记 + 退避（#2039）。
   */
  const fillGap = useCallback(() => {
    if (gapFillInFlightRef.current) {
      // #1278：已有请求在途——记下「仍需补」，由在途请求结束后再发起一轮；
      // 否则该行段要等下一个 live 批次才可能被补（run 静默/转终态即永久丢）。
      pendingGapRef.current = true;
      return;
    }
    if (Date.now() < gapFillBlockedUntilRef.current) return;
    const requestRunId = consoleRunId;
    gapFillInFlightRef.current = true;
    void (async () => {
      try {
        // 循环消费：在途期间若又出现新缺口（pendingGap 被再次置位），再补一轮。
        for (let page = 0; page < GAP_FILL_MAX_PAGES; page += 1) {
          const fromSeq = seqRef.current + 1;
          const res = await dedup.getRunLog(requestRunId, fromSeq);
          if (requestRunId !== consoleRunIdRef.current) return; // 切 run：丢弃迟到结果（#1278）
          const before = seqRef.current;
          applyLines(res.from_seq || fromSeq, res.lines);
          if (typeof res.seq === 'number' && res.seq > seqRef.current) {
            seqRef.current = res.seq;
          }
          if (typeof res.status === 'string' && res.status) {
            setStatus(res.status);
            onStatusChangeRef.current?.(res.status);
          }
          const unavailable = res.replay_unavailable === true;
          setReplayIncomplete(unavailable);
          if (unavailable) {
            gapFillBlockedUntilRef.current = Date.now() + REPLAY_UNAVAILABLE_BACKOFF_MS;
            break;
          }
          const progressed = seqRef.current > before;
          // #2070：本响应未交付完（上限截断）→ 立刻续拉下一页。
          if (res.truncated === true && progressed) continue;
          if (!progressed) break;
          if (!pendingGapRef.current) break;
          pendingGapRef.current = false;
        }
      } catch {
        /* gap fill is best-effort */
      } finally {
        gapFillInFlightRef.current = false;
      }
    })();
  }, [applyLines, consoleRunId]);

  const replayFromStart = useCallback(() => {
    let cancelled = false;
    seqRef.current = 0;
    issueKeysRef.current = new Set();
    setIssueCount(0);
    setReplayIncomplete(false);
    gapFillBlockedUntilRef.current = 0;
    termRef.current?.clear();
    dedup
      .getRunLog(consoleRunId, 0)
      .then((res) => {
        if (cancelled) return;
        applyLines(res.from_seq || 1, res.lines);
        setStatus(res.status);
        onStatusChangeRef.current?.(res.status);
        setReplayIncomplete(res.replay_unavailable === true);
        // #2070：首屏只拿到前 max_lines 行时，交给 gap fill 按页续拉；
        // 不再照旧语义把游标直接推到「全文件行数」。
        if (res.truncated === true && res.replay_unavailable !== true) fillGap();
      })
      .catch(() => {
        /* 回填失败不阻塞实时流 */
      });
    return () => {
      cancelled = true;
    };
  }, [consoleRunId, applyLines, fillGap]);

  useEffect(() => {
    if (!termReady) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 启动异步回填流；同步重置属于流的初始化
    return replayFromStart();
  }, [termReady, replayFromStart]);

  const { connectionStatus } = useSocketIO(consoleSubscription(consoleRunId), {
    onMessage: (msg: unknown) => {
      const d = msg as { run_id?: string; from_seq?: number; lines?: string[]; status?: string };
      if (!d || d.run_id !== consoleRunId) return;
      if (Array.isArray(d.lines)) {
        const from = d.from_seq ?? seqRef.current + 1;
        const expected = seqRef.current + 1;
        if (from > expected) {
          fillGap();
          return;
        }
        applyLines(from, d.lines);
      } else if (typeof d.status === 'string') {
        setStatus(d.status);
        onStatusChangeRef.current?.(d.status);
      }
    },
  });

  // Reconnect: after an earlier connected session, refill any missed lines.
  useEffect(() => {
    if (!termReady) return;
    if (connectionStatus === 'connected') {
      if (everConnectedRef.current) {
        fillGap();
      }
      everConnectedRef.current = true;
    }
  }, [connectionStatus, termReady, fillGap]);

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
      {replayIncomplete && (
        <div
          className={cn(ALERT_BANNER.warning, 'px-3 py-1 text-[11px]')}
          data-testid="live-console-replay-incomplete"
        >
          日志不完整：跨实例回放不可用（STP_RUN_CONSOLE_LOG_ROOT 未共享或落后），仅显示已交付部分。
        </div>
      )}
      <XTerminal
        ref={termRef}
        poolKey={`console-${consoleRunId}`}
        height={height}
        onReady={() => setTermReady(true)}
      />
    </div>
  );
}
