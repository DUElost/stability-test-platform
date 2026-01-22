import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../../hooks/useWebSocket';

interface LogEntry {
  timestamp: string;
  level: 'INFO' | 'DEBUG' | 'WARN' | 'ERROR' | 'FATAL';
  device: string;
  message: string;
}

export const LogViewer: React.FC<{ wsUrl: string }> = ({ wsUrl }) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [filter, setFilter] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { lastMessage } = useWebSocket<LogEntry>(wsUrl);

  useEffect(() => {
    if (lastMessage && lastMessage.type === 'LOG') {
      setLogs(prev => [...prev.slice(-1000), lastMessage.payload]);
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

  const filteredLogs = logs.filter(log =>
    log.message.toLowerCase().includes(filter.toLowerCase()) ||
    log.device.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="flex flex-col h-[500px] bg-slate-900 rounded-lg overflow-hidden border border-slate-700 shadow-xl">
      <div className="flex items-center justify-between p-2 bg-slate-800 border-b border-slate-700">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 text-xs uppercase tracking-wider font-bold px-2">Live Console</span>
          <input
            type="text"
            placeholder="Filter logs..."
            className="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2 py-1 w-48 focus:outline-none focus:border-indigo-500"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`text-xs px-2 py-1 rounded ${autoScroll ? 'bg-indigo-600 text-white' : 'bg-slate-700 text-slate-400'}`}
        >
          {autoScroll ? 'Auto-scroll ON' : 'Auto-scroll PAUSED'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-1">
        {filteredLogs.map((log, i) => (
          <div key={i} className="flex gap-2 hover:bg-slate-800/50 p-0.5 rounded">
            <span className="text-slate-500 min-w-[140px]">{log.timestamp}</span>
            <span className={`w-12 ${getLevelColor(log.level)}`}>{log.level}</span>
            <span className="text-indigo-400 min-w-[100px]">{log.device}</span>
            <span className="text-slate-300 break-all">{log.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};
