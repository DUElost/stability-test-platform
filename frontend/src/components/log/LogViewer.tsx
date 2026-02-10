import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../hooks/useWebSocket';
import { Download, Pause, Play, Trash2 } from 'lucide-react';

interface LogEntry {
  timestamp: string;
  level: 'INFO' | 'DEBUG' | 'WARN' | 'ERROR' | 'FATAL';
  device: string;
  message: string;
}

interface ProgressPayload {
  progress: number;
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
    if (!lastMessage) {
      return;
    }

    if (lastMessage.type === 'LOG') {
      const payload = lastMessage.payload as LogEntry;
      if (payload?.message) {
        setLogs(prev => [...prev.slice(-1000), payload]);
      }
    }

    if (lastMessage.type === 'PROGRESS') {
      const payload = lastMessage.payload as ProgressPayload;
      if (typeof payload?.progress === 'number') {
        const nextValue = Math.max(0, Math.min(100, payload.progress));
        setProgress(nextValue);
      }
    }
  }, [lastMessage]);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'auto' });
    }
  }, [logs, autoScroll]);

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'FATAL': return 'text-red-600 font-bold bg-red-100';
      case 'ERROR': return 'text-red-500';
      case 'WARN': return 'text-yellow-500';
      case 'DEBUG': return 'text-slate-400';
      default: return 'text-slate-300';
    }
  };

  const downloadLogs = () => {
    const content = logs.map(l => `[${l.timestamp}] [${l.level}] [${l.device}] ${l.message}`).join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs-${new Date().toISOString().replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const filteredLogs = logs.filter(log =>
    (levelFilter === 'ALL' || log.level === levelFilter) &&
    (log.message.toLowerCase().includes(filter.toLowerCase()) ||
    log.device.toLowerCase().includes(filter.toLowerCase()))
  );

  return (
    <div className="flex flex-col h-[500px] bg-slate-900 rounded-lg overflow-hidden border border-slate-700 shadow-xl">
      <div className="flex items-center justify-between p-2 bg-slate-800 border-b border-slate-700">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 text-xs uppercase tracking-wider font-bold px-2">Live Console</span>
          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className="bg-slate-900 text-xs text-slate-300 border border-slate-700 rounded px-2 py-1 focus:outline-none"
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
            className="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2 py-1 w-48 focus:outline-none focus:border-indigo-500"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setLogs([])} className="p-1 text-slate-400 hover:text-red-400" title="Clear Logs">
            <Trash2 size={14} />
          </button>
          <button onClick={downloadLogs} className="p-1 text-slate-400 hover:text-indigo-400" title="Download Logs">
            <Download size={14} />
          </button>
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`flex items-center gap-1 text-xs px-2 py-1 rounded ${autoScroll ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-400'}`}
          >
            {autoScroll ? <Pause size={12} /> : <Play size={12} />}
            {autoScroll ? 'Auto' : 'Paused'}
          </button>
        </div>
      </div>

      <div className="px-3 py-2 bg-slate-800/60 border-b border-slate-700">
        <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1">
          <span className="uppercase tracking-wider">Progress</span>
          <span className="font-mono">{progress}%</span>
        </div>
        <div className="w-full h-1.5 bg-slate-700 rounded">
          <div
            className="h-1.5 bg-indigo-500 rounded transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-1">
        {filteredLogs.map((log, i) => (
          <div key={i} className="flex gap-2 hover:bg-slate-800/50 p-0.5 rounded">
            <span className="text-slate-500 min-w-[140px]">{log.timestamp}</span>
            <span className={`w-12 ${getLevelColor(log.level)}`}>{log.level}</span>
            <span className="text-indigo-400 min-w-[100px]">{log.device}</span>
            <span className={`break-all ${
              log.message.includes('FATAL') || log.message.includes('CRASH') ? 'text-red-400 font-bold' :
              log.message.includes('ANR') ? 'text-orange-400 font-bold' :
              'text-slate-300'
            }`}>
              {log.message}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};
