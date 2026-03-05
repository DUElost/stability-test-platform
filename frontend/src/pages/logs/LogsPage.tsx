import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type RuntimeLogEntry, type Task, type TaskRun } from '@/utils/api';
import { useWebSocket, type WebSocketMessage } from '@/hooks/useWebSocket';
import {
  FileSearch,
  RefreshCw,
  Download,
  Search,
  X,
  Server,
  Smartphone,
  Database,
  Radio,
  ChevronsUp,
} from 'lucide-react';
import { CleanCard } from '@/components/ui/clean-card';
import { formatLocalDateTime, parseIsoToDate } from '@/utils/time';

interface DisplayLog extends RuntimeLogEntry {
  key: string;
  source: 'history' | 'live';
  device: string;
}

type QuickRange = '15m' | '1h' | '6h' | '24h' | 'all';

const LOG_ROW_HEIGHT = 22;
const LOG_OVERSCAN = 24;
const MAX_LOG_LINES = 30000;

function toArrayItems<T>(payload: any): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (Array.isArray(payload?.items)) return payload.items as T[];
  return [];
}

function computeFromTs(range: QuickRange): string | undefined {
  if (range === 'all') return undefined;
  const now = Date.now();
  const offsetMap: Record<Exclude<QuickRange, 'all'>, number> = {
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '6h': 6 * 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
  };
  return new Date(now - offsetMap[range]).toISOString();
}

function buildDedupeKey(log: {
  stream_id?: string;
  job_id?: number | null;
  step_id?: string;
  level?: string;
  timestamp?: string;
  message?: string;
}): string {
  if (log.stream_id) return `stream:${log.stream_id}`;
  return [
    log.job_id ?? '',
    log.step_id ?? '',
    log.level ?? '',
    log.timestamp ?? '',
    log.message ?? '',
  ].join('|');
}

interface LogsPageProps {
  embedded?: boolean;
}

export default function LogsPage({ embedded = false }: LogsPageProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskRuns, setTaskRuns] = useState<TaskRun[]>([]);

  const [taskLoading, setTaskLoading] = useState(false);
  const [runLoading, setRunLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [runsHydrated, setRunsHydrated] = useState(false);

  const [taskSearch, setTaskSearch] = useState('');
  const [runSearch, setRunSearch] = useState('');

  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  const [logs, setLogs] = useState<DisplayLog[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const [keyword, setKeyword] = useState('');
  const [stepFilter, setStepFilter] = useState('');
  const [levelFilter, setLevelFilter] = useState<string>('all');
  const [quickRange, setQuickRange] = useState<QuickRange>('1h');

  const [autoScroll, setAutoScroll] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(320);
  const [atBottom, setAtBottom] = useState(true);

  const logViewportRef = useRef<HTMLDivElement>(null);
  const seenLogKeysRef = useRef<Set<string>>(new Set());

  const fromTs = useMemo(() => computeFromTs(quickRange), [quickRange]);
  const toTs = useMemo(() => (quickRange === 'all' ? undefined : new Date().toISOString()), [quickRange]);

  const selectedTask = useMemo(
    () => tasks.find((t) => t.id === selectedTaskId) || null,
    [tasks, selectedTaskId],
  );

  const selectedRun = useMemo(
    () => taskRuns.find((r) => r.id === selectedRunId) || null,
    [taskRuns, selectedRunId],
  );

  const filteredTasks = useMemo(() => {
    const q = taskSearch.trim().toLowerCase();
    if (!q) return tasks;
    return tasks.filter((task) => (
      task.name.toLowerCase().includes(q)
      || task.status.toLowerCase().includes(q)
      || task.type.toLowerCase().includes(q)
    ));
  }, [tasks, taskSearch]);

  const filteredRuns = useMemo(() => {
    const q = runSearch.trim().toLowerCase();
    if (!q) return taskRuns;
    return taskRuns.filter((run) => (
      String(run.id).includes(q)
      || run.status.toLowerCase().includes(q)
      || (run.progress_message || '').toLowerCase().includes(q)
    ));
  }, [taskRuns, runSearch]);

  const aggregateJobIds = useMemo(() => {
    if (selectedRunId || selectedTaskId === null) return undefined;
    const ids = taskRuns.map((r) => r.id).slice(0, 180);
    return ids.length > 0 ? ids : undefined;
  }, [selectedRunId, selectedTaskId, taskRuns]);

  const wsUrl = selectedRunId
    ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/jobs/${selectedRunId}/logs`
    : '';

  const ingestLogs = useCallback((incoming: RuntimeLogEntry[], mode: 'replace' | 'append' | 'prepend') => {
    if (incoming.length === 0) {
      if (mode === 'replace') {
        seenLogKeysRef.current.clear();
        setLogs([]);
      }
      return;
    }

    setLogs((prev) => {
      const seen = seenLogKeysRef.current;
      let base: DisplayLog[] = prev;
      if (mode === 'replace') {
        seen.clear();
        base = [];
      }

      const accepted: DisplayLog[] = [];
      incoming.forEach((item) => {
        const key = buildDedupeKey(item);
        if (seen.has(key)) return;
        seen.add(key);

        accepted.push({
          ...item,
          key,
          source: item.stream_id ? 'history' : 'live',
          device: `job-${item.job_id ?? 'na'}`,
        });
      });

      if (accepted.length === 0) return base;

      const merged = mode === 'prepend'
        ? [...accepted, ...base]
        : [...base, ...accepted];

      if (merged.length > MAX_LOG_LINES) {
        const trimmed = merged.slice(merged.length - MAX_LOG_LINES);
        seenLogKeysRef.current = new Set(trimmed.map((x) => x.key));
        return trimmed;
      }

      return merged;
    });
  }, []);

  const { isConnected } = useWebSocket(
    wsUrl,
    {
      enabled: !!selectedRunId,
      onMessage: (msg: WebSocketMessage<any>) => {
        if (!selectedRunId) return;

        if (msg.type === 'STEP_LOG' && msg.payload) {
          const payload = msg.payload as any;
          ingestLogs([
            {
              job_id: selectedRunId,
              step_id: payload.step_id || '',
              level: payload.level || 'INFO',
              timestamp: payload.ts || new Date().toISOString(),
              message: payload.msg || '',
            },
          ], 'append');
          return;
        }

        if (msg.type === 'LOG' && msg.payload) {
          const payload = msg.payload as any;
          ingestLogs([
            {
              stream_id: payload.stream_id,
              job_id: payload.job_id ?? selectedRunId,
              step_id: payload.step_id || payload.tag || '',
              level: payload.level || 'INFO',
              timestamp: payload.timestamp || new Date().toISOString(),
              message: payload.message || payload.msg || '',
            },
          ], 'append');
        }
      },
    },
  );

  const loadTasks = useCallback(async () => {
    setTaskLoading(true);
    try {
      const response = await api.tasks.list(0, 200);
      setTasks(toArrayItems<Task>(response.data));
    } catch (error) {
      console.error('加载任务失败:', error);
      setTasks([]);
    } finally {
      setTaskLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async (taskId: number | null) => {
    setRunsHydrated(false);
    setRunLoading(true);
    try {
      const targetTaskId = taskId ?? 0;
      const response = await api.tasks.getRuns(targetTaskId, 0, 200);
      const items = toArrayItems<TaskRun>(response.data);
      setTaskRuns(items);
      setSelectedRunId((prev) => {
        if (prev && items.some((run) => run.id === prev)) return prev;
        if (taskId === null) return null;
        return items.length > 0 ? items[0].id : null;
      });
    } catch (error) {
      console.error('加载执行节点失败:', error);
      setTaskRuns([]);
      setSelectedRunId(null);
    } finally {
      setRunLoading(false);
      setRunsHydrated(true);
    }
  }, []);

  const fetchHistory = useCallback(async (mode: 'replace' | 'prepend', cursor?: string | null) => {
    const isReplace = mode === 'replace';
    if (isReplace) {
      setHistoryLoading(true);
    } else {
      setLoadingOlder(true);
    }

    try {
      const response = await api.tasks.queryLogs({
        job_id: selectedRunId ?? undefined,
        job_ids: !selectedRunId ? aggregateJobIds : undefined,
        level: levelFilter !== 'all' ? levelFilter : undefined,
        q: keyword.trim() || undefined,
        step_id: stepFilter.trim() || undefined,
        from_ts: fromTs,
        to_ts: toTs,
        cursor: cursor || undefined,
        limit: 300,
      });

      const payload = response.data;
      const items = Array.isArray(payload?.items) ? payload.items : [];
      ingestLogs(items, mode);
      setNextCursor(payload?.next_cursor ?? null);
      setHasMore(!!payload?.has_more);
    } catch (error) {
      console.error('加载历史日志失败:', error);
      if (isReplace) {
        ingestLogs([], 'replace');
      }
      setNextCursor(null);
      setHasMore(false);
    } finally {
      if (isReplace) {
        setHistoryLoading(false);
      } else {
        setLoadingOlder(false);
      }
    }
  }, [aggregateJobIds, fromTs, ingestLogs, keyword, levelFilter, selectedRunId, stepFilter, toTs]);

  const handleLoadOlder = async () => {
    if (!hasMore || !nextCursor || loadingOlder) return;
    await fetchHistory('prepend', nextCursor);
  };

  const handleRefreshLogs = async () => {
    await fetchHistory('replace', null);
  };

  const clearLogs = () => {
    seenLogKeysRef.current.clear();
    setLogs([]);
  };

  const downloadLogs = () => {
    const content = filteredLogs.map((log) => (
      `[${formatLocalDateTime(log.timestamp, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })}] [${log.level}] [job-${log.job_id ?? 'na'}] [${log.step_id || '-'}] ${log.message}`
    )).join('\n');

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `runtime_logs_${selectedTask?.name || 'all'}_${selectedRunId || 'aggregate'}_${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const filteredLogs = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    const stepKey = stepFilter.trim().toLowerCase();
    const fromDate = fromTs ? parseIsoToDate(fromTs) : null;
    const toDate = toTs ? parseIsoToDate(toTs) : null;

    return logs.filter((log) => {
      if (levelFilter !== 'all' && log.level !== levelFilter) return false;
      if (key) {
        const line = `${log.message}\n${log.step_id || ''}\njob-${log.job_id ?? ''}`.toLowerCase();
        if (!line.includes(key)) return false;
      }
      if (stepKey && !(log.step_id || '').toLowerCase().includes(stepKey)) return false;

      if (fromDate || toDate) {
        const logDate = parseIsoToDate(log.timestamp);
        if (!logDate) return false;
        if (fromDate && logDate < fromDate) return false;
        if (toDate && logDate > toDate) return false;
      }
      return true;
    });
  }, [fromTs, keyword, levelFilter, logs, stepFilter, toTs]);

  const totalHeight = filteredLogs.length * LOG_ROW_HEIGHT;
  const startIndex = Math.max(0, Math.floor(scrollTop / LOG_ROW_HEIGHT) - LOG_OVERSCAN);
  const endIndex = Math.min(
    filteredLogs.length,
    Math.ceil((scrollTop + viewportHeight) / LOG_ROW_HEIGHT) + LOG_OVERSCAN,
  );
  const visibleLogs = filteredLogs.slice(startIndex, endIndex);

  const handleLogScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    setScrollTop(el.scrollTop);
    setAtBottom(el.scrollTop + el.clientHeight >= el.scrollHeight - 40);
  };

  useEffect(() => {
    const el = logViewportRef.current;
    if (!el) return;

    const updateHeight = () => setViewportHeight(el.clientHeight);
    updateHeight();

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(updateHeight);
      observer.observe(el);
      return () => observer.disconnect();
    }

    window.addEventListener('resize', updateHeight);
    return () => window.removeEventListener('resize', updateHeight);
  }, []);

  useEffect(() => {
    if (!autoScroll || !atBottom) return;
    const el = logViewportRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [filteredLogs.length, autoScroll, atBottom]);

  useEffect(() => {
    void loadTasks();
    const timer = setInterval(() => {
      void loadTasks();
    }, 30000);
    return () => clearInterval(timer);
  }, [loadTasks]);

  useEffect(() => {
    setTaskRuns([]);
    setSelectedRunId(null);
    void loadRuns(selectedTaskId);
  }, [loadRuns, selectedTaskId]);

  useEffect(() => {
    if (!runsHydrated) return;
    const timer = setTimeout(() => {
      void fetchHistory('replace', null);
    }, 250);
    return () => clearTimeout(timer);
  }, [fetchHistory, keyword, stepFilter, levelFilter, quickRange, selectedRunId, selectedTaskId, runsHydrated]);

  const overallProgress = useMemo(() => {
    if (taskRuns.length === 0) return 0;
    const total = taskRuns.reduce((sum, run) => sum + (run.progress || 0), 0);
    return Math.round(total / taskRuns.length);
  }, [taskRuns]);

  const isLiveMode = !!selectedRunId;

  return (
    <div className={`${embedded ? 'min-h-[72vh]' : 'h-full'} flex flex-col`}>
      {!embedded && (
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-slate-100 p-2">
              <FileSearch className="h-6 w-6 text-slate-700" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold text-gray-900">日志总览</h1>
              <p className="text-sm text-gray-500">
                {isLiveMode ? (isConnected ? '实时连接中' : '实时未连接') : '历史聚合模式'}
                {selectedRunId && ` | Job: #${selectedRunId}`}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                void loadTasks();
                void loadRuns(selectedTaskId);
                void handleRefreshLogs();
              }}
              className="flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              <RefreshCw className="h-4 w-4" />
              刷新
            </button>
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="w-80 flex-shrink-0">
          <CleanCard className="flex h-full flex-col overflow-hidden">
            <div className="border-b border-gray-100 p-3">
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-gray-900">任务视图</h3>
                <span className="text-xs text-gray-400">{tasks.length}</span>
              </div>
              <div className="relative mt-2">
                <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
                <input
                  value={taskSearch}
                  onChange={(e) => setTaskSearch(e.target.value)}
                  placeholder="搜索任务"
                  className="w-full rounded-lg border border-gray-300 py-1.5 pl-7 pr-2 text-xs focus:border-slate-500 focus:outline-none"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              <button
                type="button"
                onClick={() => setSelectedTaskId(null)}
                className={`w-full border-b border-gray-100 px-4 py-3 text-left text-sm transition-colors ${
                  selectedTaskId === null ? 'bg-slate-50' : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-gray-900">全部任务</span>
                  <Database className="h-4 w-4 text-gray-400" />
                </div>
                <p className="mt-1 text-xs text-gray-500">跨任务聚合日志</p>
              </button>

              {taskLoading ? (
                <div className="p-4 text-center text-sm text-gray-400">加载中...</div>
              ) : filteredTasks.length === 0 ? (
                <div className="p-4 text-center text-sm text-gray-400">无匹配任务</div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {filteredTasks.map((task) => (
                    <button
                      key={task.id}
                      type="button"
                      onClick={() => setSelectedTaskId(task.id)}
                      className={`w-full px-4 py-3 text-left transition-colors ${
                        selectedTaskId === task.id ? 'bg-blue-50' : 'hover:bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="truncate text-sm font-medium text-gray-900">{task.name}</span>
                        <span className="rounded bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">{task.status}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-500">
                        <span>{task.type}</span>
                        {task.runs_count != null && <span>{task.runs_count} runs</span>}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </CleanCard>
        </div>

        <div className="w-72 flex-shrink-0">
          <CleanCard className="flex h-full flex-col overflow-hidden">
            <div className="border-b border-gray-100 p-3">
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-gray-900">执行节点</h3>
                <span className="text-xs text-gray-400">{taskRuns.length}</span>
              </div>
              <div className="mt-2">
                <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
                  <span>整体进度</span>
                  <span>{overallProgress}%</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-gray-100">
                  <div className="h-1.5 rounded-full bg-slate-600" style={{ width: `${overallProgress}%` }} />
                </div>
              </div>
              <div className="relative mt-2">
                <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
                <input
                  value={runSearch}
                  onChange={(e) => setRunSearch(e.target.value)}
                  placeholder="搜索 Job"
                  className="w-full rounded-lg border border-gray-300 py-1.5 pl-7 pr-2 text-xs focus:border-slate-500 focus:outline-none"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              <button
                type="button"
                onClick={() => setSelectedRunId(null)}
                className={`w-full border-b border-gray-100 px-3 py-3 text-left transition-colors ${
                  selectedRunId === null ? 'bg-slate-50' : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-900">全部执行节点</span>
                  <Server className="h-4 w-4 text-gray-400" />
                </div>
                <p className="mt-1 text-[11px] text-gray-500">当前任务范围内聚合</p>
              </button>

              {runLoading ? (
                <div className="p-4 text-center text-sm text-gray-400">加载中...</div>
              ) : filteredRuns.length === 0 ? (
                <div className="p-4 text-center text-sm text-gray-400">暂无执行节点</div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {filteredRuns.map((run) => (
                    <button
                      key={run.id}
                      type="button"
                      onClick={() => setSelectedRunId(run.id)}
                      className={`w-full px-3 py-3 text-left transition-colors ${
                        selectedRunId === run.id ? 'bg-blue-50' : 'hover:bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5">
                          <Smartphone className="h-4 w-4 text-gray-400" />
                          <span className="text-sm font-medium text-gray-900">Job {run.id}</span>
                        </div>
                        <span className="rounded bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">{run.status}</span>
                      </div>
                      <div className="mt-1 text-[11px] text-gray-500">{run.progress_message || '无进度描述'}</div>
                      <div className="mt-2 h-1.5 w-full rounded-full bg-gray-100">
                        <div className="h-1.5 rounded-full bg-blue-500" style={{ width: `${run.progress || 0}%` }} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </CleanCard>
        </div>

        <div className="min-w-0 flex-1">
          <CleanCard className="flex h-full flex-col overflow-hidden">
            <div className="border-b border-gray-100 p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    void handleLoadOlder();
                  }}
                  disabled={!hasMore || loadingOlder || historyLoading}
                  className="flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  <ChevronsUp className="h-3.5 w-3.5" />
                  {loadingOlder ? '加载中...' : '加载更早日志'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void handleRefreshLogs();
                  }}
                  disabled={historyLoading}
                  className="flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {historyLoading ? '刷新中...' : '刷新历史'}
                </button>
                <button
                  type="button"
                  onClick={() => setAutoScroll((v) => !v)}
                  className={`flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs ${
                    autoScroll
                      ? 'border-blue-200 bg-blue-50 text-blue-700'
                      : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <Radio className="h-3.5 w-3.5" />
                  自动滚动
                </button>
                <button
                  type="button"
                  onClick={clearLogs}
                  className="rounded-lg border border-gray-300 bg-white p-1.5 text-gray-600 hover:bg-gray-50"
                  title="清空已加载日志"
                >
                  <X className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={downloadLogs}
                  disabled={filteredLogs.length === 0}
                  className="rounded-lg border border-gray-300 bg-white p-1.5 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  title="下载当前过滤结果"
                >
                  <Download className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
                <div className="relative md:col-span-2">
                  <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="关键词（message / step / job）"
                    className="w-full rounded-lg border border-gray-300 py-1.5 pl-7 pr-2 text-xs focus:border-slate-500 focus:outline-none"
                  />
                </div>
                <input
                  value={stepFilter}
                  onChange={(e) => setStepFilter(e.target.value)}
                  placeholder="step_id"
                  className="rounded-lg border border-gray-300 px-2 py-1.5 text-xs focus:border-slate-500 focus:outline-none"
                />
                <select
                  value={levelFilter}
                  onChange={(e) => setLevelFilter(e.target.value)}
                  className="rounded-lg border border-gray-300 px-2 py-1.5 text-xs focus:border-slate-500 focus:outline-none"
                >
                  <option value="all">全部级别</option>
                  <option value="DEBUG">DEBUG</option>
                  <option value="INFO">INFO</option>
                  <option value="WARN">WARN</option>
                  <option value="ERROR">ERROR</option>
                </select>
                <select
                  value={quickRange}
                  onChange={(e) => setQuickRange(e.target.value as QuickRange)}
                  className="rounded-lg border border-gray-300 px-2 py-1.5 text-xs focus:border-slate-500 focus:outline-none"
                >
                  <option value="15m">最近 15 分钟</option>
                  <option value="1h">最近 1 小时</option>
                  <option value="6h">最近 6 小时</option>
                  <option value="24h">最近 24 小时</option>
                  <option value="all">全部时间</option>
                </select>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-gray-500">
                <span>范围: {selectedTask ? selectedTask.name : '全部任务'}</span>
                <span>模式: {selectedRun ? `单 Job #${selectedRun.id}` : '聚合模式'}</span>
                <span>已加载: {logs.length}</span>
                <span>过滤后: {filteredLogs.length}</span>
              </div>
            </div>

            <div
              ref={logViewportRef}
              className="flex-1 overflow-y-auto bg-gray-900 p-2 font-mono text-xs"
              onScroll={handleLogScroll}
            >
              {historyLoading && logs.length === 0 ? (
                <div className="py-8 text-center text-gray-500">加载日志中...</div>
              ) : filteredLogs.length === 0 ? (
                <div className="py-8 text-center text-gray-500">
                  {selectedTaskId === null && !selectedRunId
                    ? '暂无日志，已处于跨任务聚合模式'
                    : selectedRunId
                      ? '等待日志...' : '请选择 Job 或调整过滤条件'}
                </div>
              ) : (
                <div style={{ height: totalHeight, position: 'relative' }}>
                  <div style={{ transform: `translateY(${startIndex * LOG_ROW_HEIGHT}px)` }}>
                    {visibleLogs.map((log) => (
                      <div
                        key={log.key}
                        className={`truncate leading-[22px] ${
                          log.level === 'ERROR'
                            ? 'text-red-400'
                            : log.level === 'WARN'
                              ? 'text-yellow-400'
                              : 'text-gray-300'
                        }`}
                        style={{ height: LOG_ROW_HEIGHT }}
                        title={`${formatLocalDateTime(log.timestamp, {
                          year: 'numeric',
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                        })} [${log.level}] [job-${log.job_id ?? 'na'}] [${log.step_id || '-'}] ${log.message}`}
                      >
                        <span className="text-gray-500">
                          [{formatLocalDateTime(log.timestamp, {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          })}]
                        </span>{' '}
                        <span className="text-blue-400">[job-{log.job_id ?? 'na'}]</span>{' '}
                        <span className="text-purple-300">[{log.step_id || '-'}]</span>{' '}
                        <span className={log.level === 'ERROR' ? 'text-red-400' : log.level === 'WARN' ? 'text-yellow-400' : 'text-green-400'}>
                          [{log.level}]
                        </span>{' '}
                        {log.message}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-500">
              <span>
                {isLiveMode ? (
                  <span className={`inline-flex items-center gap-1 ${isConnected ? 'text-green-600' : 'text-gray-400'}`}>
                    <span className={`h-2 w-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-gray-400'}`} />
                    {isConnected ? '单 Job 实时订阅已连接' : '单 Job 实时订阅未连接'}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-slate-600">
                    <Database className="h-3.5 w-3.5" />
                    聚合历史查询模式（非实时）
                  </span>
                )}
              </span>
              <span>has_more: {hasMore ? 'yes' : 'no'}</span>
            </div>
          </CleanCard>
        </div>
      </div>
    </div>
  );
}
