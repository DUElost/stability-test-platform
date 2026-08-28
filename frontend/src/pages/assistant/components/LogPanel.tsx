import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/utils/api';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { cn } from '@/lib/utils';

interface LogPanelProps {
  actionId: number;
  /** running/approved 时开启轮询（2s），终态或未开始时关闭。 */
  active: boolean;
  className?: string;
}

/**
 * 长命令执行日志（RunConsole 落盘日志的只读视图）。
 * v1 轮询拉全量（日志量有限），增量 from_seq 增量拉取留作优化。
 */
export function LogPanel({ actionId, active, className }: LogPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const logQ = useQuery({
    queryKey: aiAssistantKeys.actionLog(actionId),
    queryFn: () => api.aiAssistant.getActionLog(actionId),
    enabled: active,
    refetchInterval: active ? 2000 : false,
  });

  const lines = logQ.data ?? [];
  const lineCount = lines.length;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lineCount]);

  return (
    <div
      ref={scrollRef}
      className={cn(
        'max-h-60 overflow-y-auto rounded-md bg-muted/60 p-3 font-mono text-xs leading-relaxed',
        className,
      )}
      aria-label="执行日志"
    >
      {lines.length === 0 ? (
        <span className="text-muted-foreground">
          {active ? '暂无输出，等待执行…' : '暂无输出'}
        </span>
      ) : (
        lines.map((entry) => (
          <div
            key={entry.seq}
            className={cn('break-all whitespace-pre-wrap', entry.stream === 'stderr' && 'text-destructive')}
          >
            {entry.line}
          </div>
        ))
      )}
    </div>
  );
}

export default LogPanel;
