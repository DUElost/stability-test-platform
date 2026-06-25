import React, { useEffect, useRef, useState } from 'react';
import { useSocketIO as useWebSocket } from '../../hooks/useSocketIO';
import { Download, Pause, Play, Trash2 } from 'lucide-react';
import { FORM, INTERACTIVE, LOG_LEVEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface LogEntry {
  timestamp: string;
  level: 'INFO' | 'DEBUG' | 'WARN' | 'ERROR' | 'FATAL';
  device: string;
  message: string;
}

interface ProgressPayload {
  progress: number;
}

function getLevelColor(level: string): string {
  switch (level) {
    case 'FATAL':
      return LOG_LEVEL.fatal;
    case 'ERROR':
      return LOG_LEVEL.error;
    case 'WARN':
      return LOG_LEVEL.warn;
    case 'DEBUG':
      return LOG_LEVEL.debug;
    default:
      return LOG_LEVEL.default;
  }
}

export const LogViewer: React.FC<{ wsUrl: string }> = ({ wsUrl }) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [progress, setProgress] = useState<number>(0);
  const [filter, setFilter] = useState('');
  const [levelFilter, setLevelFilter] = useState<string>('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { lastMessage } = useWebSocket<LogEntry | ProgressPayload>(wsUrl);

  useEffect(() => {
    if (!lastMessage) return;

    if (lastMessage.type === 'LOG') {
      const payload = lastMessage.payload as LogEntry;
      if (payload?.message) {
        setLogs((prev) => [...prev.slice(-1000), payload]);
      }
    }

    if (lastMessage.type === 'PROGRESS') {
      const payload = lastMessage.payload as ProgressPayload;
      if (typeof payload?.progress === 'number') {
        setProgress(Math.max(0, Math.min(100, payload.progress)));
      }
    }
  }, [lastMessage]);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'auto' });
    }
  }, [logs, autoScroll]);

  const downloadLogs = () => {
    const content = logs
      .map((l) => `[${l.timestamp}] [${l.level}] [${l.device}] ${l.message}`)
      .join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs-${new Date().toISOString().replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const filteredLogs = logs.filter(
    (log) =>
      (levelFilter === 'ALL' || log.level === levelFilter) &&
      (log.message.toLowerCase().includes(filter.toLowerCase()) ||
        log.device.toLowerCase().includes(filter.toLowerCase())),
  );

  return (
    <div className="dark flex h-[500px] flex-col overflow-hidden rounded-lg border border-border bg-background shadow-xl">
      <div className="flex items-center justify-between border-b border-border bg-muted p-2">
        <div className="flex items-center gap-2">
          <span className={cn('px-2 text-xs font-bold uppercase tracking-wider', TEXT.subtitle)}>
            Live Console
          </span>
          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className={cn(FORM.selectSm, 'bg-background')}
          >
            <option value="ALL">ALL LEVELS</option>
            <option value="INFO">INFO</option>
            <option value="WARN">WARN</option>
            <option value="ERROR">ERROR</option>
            <option value="FATAL">FATAL</option>
          </select>
          <input
            type="text"
            placeholder="Filter logs..."
            className={cn(FORM.inputSm, 'w-48 bg-background pl-2')}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setLogs([])}
            className={cn('p-1 hover:text-destructive', INTERACTIVE.iconButton)}
            title="Clear Logs"
          >
            <Trash2 size={14} />
          </button>
          <button
            onClick={downloadLogs}
            className={cn('p-1 hover:text-primary', INTERACTIVE.iconButton)}
            title="Download Logs"
          >
            <Download size={14} />
          </button>
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={cn(
              'flex items-center gap-1 rounded px-2 py-1 text-xs',
              autoScroll ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground',
            )}
          >
            {autoScroll ? <Pause size={12} /> : <Play size={12} />}
            {autoScroll ? 'Auto' : 'Paused'}
          </button>
        </div>
      </div>

      <div className="border-b border-border bg-muted/60 px-3 py-2">
        <div className={cn('mb-1 flex items-center justify-between text-[11px]', TEXT.subtitle)}>
          <span className="uppercase tracking-wider">Progress</span>
          <span className="font-mono">{progress}%</span>
        </div>
        <div className="h-1.5 w-full rounded bg-muted">
          <div
            className="h-1.5 rounded bg-primary transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto p-4 font-mono text-xs">
        {filteredLogs.map((log, i) => (
          <div key={i} className="flex gap-2 rounded p-0.5 hover:bg-accent/50">
            <span className={cn('min-w-[140px]', TEXT.subtitle)}>{log.timestamp}</span>
            <span className={cn('w-12', getLevelColor(log.level))}>{log.level}</span>
            <span className="min-w-[100px] text-primary">{log.device}</span>
            <span
              className={cn(
                'break-all',
                log.message.includes('FATAL') || log.message.includes('CRASH')
                  ? cn(LOG_LEVEL.error, 'font-bold')
                  : log.message.includes('ANR')
                    ? cn(LOG_LEVEL.warn, 'font-bold')
                    : LOG_LEVEL.default,
              )}
            >
              {log.message}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};
