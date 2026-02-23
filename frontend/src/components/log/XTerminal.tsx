import React, { useEffect, useRef, useState, useCallback, useImperativeHandle } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { SearchAddon } from '@xterm/addon-search';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { ArrowDown, Download, Search, X, ChevronUp, ChevronDown, Regex } from 'lucide-react';

// ---------- ANSI helpers ----------

const ANSI = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  // Log level colors
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  gray: '\x1b[90m',
  white: '\x1b[37m',
  // Keyword highlights
  boldRed: '\x1b[1;31m',
  boldYellow: '\x1b[1;33m',
};

function colorizeLogLevel(level: string): string {
  switch (level.toUpperCase()) {
    case 'FATAL':
    case 'ERROR':
      return ANSI.red;
    case 'WARN':
      return ANSI.yellow;
    case 'DEBUG':
      return ANSI.gray;
    default:
      return ANSI.white;
  }
}

const KEYWORD_PATTERNS: Array<{ regex: RegExp; ansi: string }> = [
  { regex: /\b(FATAL|CRASH)\b/gi, ansi: ANSI.boldRed },
  { regex: /\bANR\b/gi, ansi: ANSI.boldYellow },
];

function highlightKeywords(text: string): string {
  let result = text;
  for (const { regex, ansi } of KEYWORD_PATTERNS) {
    result = result.replace(regex, (match) => `${ansi}${match}${ANSI.reset}`);
  }
  return result;
}

function formatLogLine(line: string, level?: string): string {
  // Detect fold group markers (OSC 633 protocol)
  const foldStartMatch = line.match(/\x1b\]633;A\x07(.+)/);
  if (foldStartMatch) {
    const title = foldStartMatch[1];
    return `${ANSI.bold}\x1b[36m${'─'.repeat(4)} ▼ ${title} ${'─'.repeat(40)}${ANSI.reset}`;
  }
  const foldEndMatch = line.match(/\x1b\]633;B\x07(.+)/);
  if (foldEndMatch) {
    const title = foldEndMatch[1];
    const isFailure = title.includes('FAILED');
    const color = isFailure ? ANSI.red : '\x1b[32m';
    return `${ANSI.bold}${color}${'─'.repeat(4)} ▲ ${title} ${'─'.repeat(40)}${ANSI.reset}`;
  }

  const color = level ? colorizeLogLevel(level) : '';
  const highlighted = highlightKeywords(line);
  return level ? `${color}${highlighted}${ANSI.reset}` : highlighted;
}

// Strip ANSI codes and OSC sequences for plain text download
function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '').replace(/\x1b\][0-9]+;[^\x07]*\x07/g, '');
}

// ---------- Terminal instance pool ----------

interface PoolEntry {
  terminal: Terminal;
  fitAddon: FitAddon;
  searchAddon: SearchAddon;
  lastUsed: number;
}

const terminalPool: Map<string, PoolEntry> = new Map();
const MAX_POOL_SIZE = 3;

function getOrCreateTerminal(key: string): PoolEntry {
  const existing = terminalPool.get(key);
  if (existing) {
    existing.lastUsed = Date.now();
    return existing;
  }

  // Evict LRU if pool is full
  if (terminalPool.size >= MAX_POOL_SIZE) {
    let oldestKey = '';
    let oldestTime = Infinity;
    for (const [k, v] of terminalPool) {
      if (v.lastUsed < oldestTime) {
        oldestTime = v.lastUsed;
        oldestKey = k;
      }
    }
    if (oldestKey) {
      const evicted = terminalPool.get(oldestKey);
      evicted?.terminal.dispose();
      terminalPool.delete(oldestKey);
    }
  }

  const terminal = new Terminal({
    theme: {
      background: '#0f172a', // slate-900
      foreground: '#cbd5e1', // slate-300
      cursor: '#6366f1',     // indigo-500
      selectionBackground: '#334155', // slate-700
      black: '#0f172a',
      red: '#ef4444',
      green: '#22c55e',
      yellow: '#eab308',
      blue: '#3b82f6',
      magenta: '#a855f7',
      cyan: '#06b6d4',
      white: '#f1f5f9',
    },
    fontSize: 12,
    fontFamily: '"SF Mono", Monaco, "Cascadia Code", "Fira Code", monospace',
    cursorBlink: false,
    cursorStyle: 'bar',
    disableStdin: true,
    convertEol: true,
    scrollback: 10000,
    allowTransparency: true,
  });

  const fitAddon = new FitAddon();
  const searchAddon = new SearchAddon();
  const webLinksAddon = new WebLinksAddon();

  terminal.loadAddon(fitAddon);
  terminal.loadAddon(searchAddon);
  terminal.loadAddon(webLinksAddon);

  const entry: PoolEntry = { terminal, fitAddon, searchAddon, lastUsed: Date.now() };
  terminalPool.set(key, entry);
  return entry;
}

function releaseTerminal(key: string): void {
  const entry = terminalPool.get(key);
  if (entry) {
    entry.terminal.dispose();
    terminalPool.delete(key);
  }
}

// ---------- Component types ----------

export interface XTerminalHandle {
  writeLine: (line: string, level?: string) => void;
  writeLines: (lines: Array<{ msg: string; level?: string }>) => void;
  clear: () => void;
  scrollToBottom: () => void;
}

export interface XTerminalProps {
  /** Unique key for terminal pool (e.g. "run_1_step_2") */
  poolKey: string;
  /** Run ID for download filename */
  runId?: number;
  /** Step name for download filename */
  stepName?: string;
  /** Height CSS value */
  height?: string;
  /** Called when the component mounts; parent can push lines via ref */
  onReady?: () => void;
}

// ---------- Component ----------

export const XTerminal = React.forwardRef<XTerminalHandle, XTerminalProps>(
  ({ poolKey, runId, stepName, height = '500px', onReady }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const poolEntryRef = useRef<PoolEntry | null>(null);
    const linesBufferRef = useRef<string[]>([]);
    const autoScrollRef = useRef(true);
    const [autoScroll, setAutoScroll] = useState(true);
    const [showSearch, setShowSearch] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [useRegex, setUseRegex] = useState(false);
    const searchInputRef = useRef<HTMLInputElement>(null);

    // Sync autoScroll state with ref for event callbacks
    useEffect(() => {
      autoScrollRef.current = autoScroll;
    }, [autoScroll]);

    // Initialize terminal
    useEffect(() => {
      if (!containerRef.current) return;

      const entry = getOrCreateTerminal(poolKey);
      poolEntryRef.current = entry;
      const { terminal, fitAddon } = entry;

      // Clear container and open terminal
      containerRef.current.innerHTML = '';
      terminal.open(containerRef.current);

      // Fit to container
      try {
        fitAddon.fit();
      } catch {
        // Fit may fail if container not yet laid out
      }

      // Detect manual scroll → pause auto-scroll
      const viewport = containerRef.current.querySelector('.xterm-viewport');
      const handleScroll = () => {
        if (!viewport) return;
        const { scrollTop, scrollHeight, clientHeight } = viewport;
        const atBottom = scrollHeight - scrollTop - clientHeight < 30;
        if (!atBottom && autoScrollRef.current) {
          setAutoScroll(false);
        }
      };
      viewport?.addEventListener('scroll', handleScroll);

      // Debounced resize handler
      let resizeTimer: ReturnType<typeof setTimeout>;
      const resizeObserver = new ResizeObserver(() => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          try {
            fitAddon.fit();
          } catch {
            // ignore
          }
        }, 200);
      });
      resizeObserver.observe(containerRef.current);

      onReady?.();

      return () => {
        clearTimeout(resizeTimer);
        resizeObserver.disconnect();
        viewport?.removeEventListener('scroll', handleScroll);
      };
    }, [poolKey]); // Re-initialize when poolKey changes

    // Expose imperative handle
    const writeLine = useCallback((line: string, level?: string) => {
      const entry = poolEntryRef.current;
      if (!entry) return;
      const formatted = formatLogLine(line, level);
      entry.terminal.writeln(formatted);
      linesBufferRef.current.push(line);
      if (autoScrollRef.current) {
        entry.terminal.scrollToBottom();
      }
    }, []);

    const writeLines = useCallback((lines: Array<{ msg: string; level?: string }>) => {
      const entry = poolEntryRef.current;
      if (!entry) return;
      for (const { msg, level } of lines) {
        const formatted = formatLogLine(msg, level);
        entry.terminal.writeln(formatted);
        linesBufferRef.current.push(msg);
      }
      if (autoScrollRef.current) {
        entry.terminal.scrollToBottom();
      }
    }, []);

    const clear = useCallback(() => {
      poolEntryRef.current?.terminal.clear();
      linesBufferRef.current = [];
    }, []);

    const scrollToBottom = useCallback(() => {
      poolEntryRef.current?.terminal.scrollToBottom();
      setAutoScroll(true);
    }, []);

    useImperativeHandle(ref, () => ({
      writeLine,
      writeLines,
      clear,
      scrollToBottom,
    }), [writeLine, writeLines, clear, scrollToBottom]);

    // Search
    const doSearch = useCallback((direction: 'next' | 'prev') => {
      const addon = poolEntryRef.current?.searchAddon;
      if (!addon || !searchQuery) return;
      const opts = { regex: useRegex, caseSensitive: false };
      if (direction === 'next') {
        addon.findNext(searchQuery, opts);
      } else {
        addon.findPrevious(searchQuery, opts);
      }
    }, [searchQuery, useRegex]);

    const handleSearchKeyDown = useCallback((e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        doSearch(e.shiftKey ? 'prev' : 'next');
      } else if (e.key === 'Escape') {
        setShowSearch(false);
        poolEntryRef.current?.searchAddon.clearDecorations();
      }
    }, [doSearch]);

    // Keyboard shortcut: Ctrl+F to open search
    useEffect(() => {
      const handler = (e: KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
          const container = containerRef.current;
          if (container && container.contains(document.activeElement)) {
            e.preventDefault();
            setShowSearch(true);
            setTimeout(() => searchInputRef.current?.focus(), 50);
          }
        }
      };
      document.addEventListener('keydown', handler);
      return () => document.removeEventListener('keydown', handler);
    }, []);

    // Download handler
    const handleDownload = useCallback(() => {
      const content = linesBufferRef.current.map(stripAnsi).join('\n');
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const name = runId && stepName
        ? `run_${runId}_step_${stepName}.log`
        : `terminal_${new Date().toISOString().replace(/:/g, '-')}.log`;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, [runId, stepName]);

    return (
      <div className="flex flex-col bg-[#0f172a] rounded-lg overflow-hidden border border-slate-700 shadow-xl" style={{ height }}>
        {/* Toolbar */}
        <div className="flex items-center justify-between px-3 py-1.5 bg-slate-800 border-b border-slate-700">
          <span className="text-slate-400 text-xs uppercase tracking-wider font-bold">Terminal</span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => {
                setShowSearch(s => !s);
                if (!showSearch) setTimeout(() => searchInputRef.current?.focus(), 50);
              }}
              className="p-1 text-slate-400 hover:text-indigo-400 transition-colors"
              title="Search (Ctrl+F)"
            >
              <Search size={14} />
            </button>
            <button
              onClick={handleDownload}
              className="p-1 text-slate-400 hover:text-indigo-400 transition-colors"
              title="Download Log"
            >
              <Download size={14} />
            </button>
            {!autoScroll && (
              <button
                onClick={scrollToBottom}
                className="flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-indigo-600 text-white hover:bg-indigo-500 transition-colors"
                title="Resume auto-scroll"
              >
                <ArrowDown size={12} />
                Resume
              </button>
            )}
          </div>
        </div>

        {/* Search bar */}
        {showSearch && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-800/80 border-b border-slate-700">
            <input
              ref={searchInputRef}
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="flex-1 bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2 py-1 focus:outline-none focus:border-indigo-500"
            />
            <button
              onClick={() => setUseRegex(r => !r)}
              className={`p-1 rounded ${useRegex ? 'text-indigo-400 bg-slate-700' : 'text-slate-500 hover:text-slate-300'}`}
              title="Toggle regex"
            >
              <Regex size={14} />
            </button>
            <button onClick={() => doSearch('prev')} className="p-1 text-slate-400 hover:text-slate-200" title="Previous">
              <ChevronUp size={14} />
            </button>
            <button onClick={() => doSearch('next')} className="p-1 text-slate-400 hover:text-slate-200" title="Next">
              <ChevronDown size={14} />
            </button>
            <button
              onClick={() => {
                setShowSearch(false);
                poolEntryRef.current?.searchAddon.clearDecorations();
              }}
              className="p-1 text-slate-400 hover:text-red-400"
              title="Close search"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* Terminal container */}
        <div ref={containerRef} className="flex-1 px-1" />
      </div>
    );
  },
);

XTerminal.displayName = 'XTerminal';

export default XTerminal;

// Cleanup utility for unmounting
export function disposeAllTerminals(): void {
  for (const [key, entry] of terminalPool) {
    entry.terminal.dispose();
  }
  terminalPool.clear();
}

export { releaseTerminal };
