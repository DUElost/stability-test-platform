import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type RuntimeLogEntry, type Plan, type PlanRun } from '@/utils/api';
import { useSocketIO as useWebSocket, type WebSocketMessage } from '@/hooks/useSocketIO';
import {
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
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/layout';
import { formatLocalDateTime, parseIsoToDate } from '@/utils/time';
import {
  FORM,
  LIST_ITEM,
  LOG_LEVEL,
  PANEL,
  SEGMENTED,
  STATUS_CHIP,
  TEXT,
  TOOL_BTN,
  listItemClass,
} from '@/design-system';
import { cn } from '@/lib/utils';

interface DisplayLog extends RuntimeLogEntry {
  key: string;
  source: 'history' | 'live';
  device: string;
}

type QuickRange = '15m' | '1h' | '6h' | '24h' | 'all';

const LOG_ROW_HEIGHT = 22;
const LOG_OVERSCAN = 24;
const MAX_LOG_LINES = 30000;

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

function highlightLogText(text: string): ReactNode[] {
  const parts = text.split(/(\bFATAL\b|\bCRASH\b|\bANR\b)/gi);
  return parts.map((part, index) => (
    /^(FATAL|CRASH|ANR)$/i.test(part)
      ? <mark key={index} className={LOG_LEVEL.highlight}>{part}</mark>
      : <span key={index}>{part}</span>
  ));
}

interface LogsPageProps {
  embedded?: boolean;
}

export default function LogsPage({ embedded = false }: LogsPageProps) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [jobs, setJobs] = useState<PlanRun[]>([]);

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
    () => plans.find((t) => t.id === selectedTaskId) || null,
    [plans, selectedTaskId],
  );

  const selectedRun = useMemo(
    () => jobs.find((r) => r.id === selectedRunId) || null,
    [jobs, selectedRunId],
  );

  const filteredTasks = useMemo(() => {
    const q = taskSearch.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter((plan) => (
      plan.name.toLowerCase().includes(q)
      || (plan.description || '').toLowerCase().includes(q)
    ));
  }, [plans, taskSearch]);

  const filteredRuns = useMemo(() => {
    const q = runSearch.trim().toLowerCase();
    if (!q) return jobs;
    return jobs.filter((job) => (
      String(job.id).includes(q)
      || job.status.toLowerCase().includes(q)
    ));
  }, [jobs, runSearch]);

  const aggregateJobIds = useMemo(() => {
    if (selectedRunId || selectedTaskId === null) return undefined;
    const ids = jobs.map((r) => r.id).slice(0, 180);
    return ids.length > 0 ? ids : undefined;
  }, [selectedRunId, selectedTaskId, jobs]);

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
      const planList = await api.plans.list(0, 200);
      setPlans(Array.isArray(planList) ? planList : []);
    } catch (error) {
      console.error('加载 Plan 失败:', error);
      setPlans([]);
    } finally {
      setTaskLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async (taskId: number | null) => {
    setRunsHydrated(false);
    setRunLoading(true);
    try {
      const planId = taskId && taskId > 0 ? taskId : undefined;
      const result = await api.planRuns.list(0, 200, planId);
      setJobs(result);
      setSelectedRunId((prev) => {
        if (prev && result.some((job) => job.id === prev)) return prev;
        if (taskId === null) return null;
        return result.length > 0 ? result[0].id : null;
      });
    } catch (error) {
      console.error('加载执行记录失败:', error);
      setJobs([]);
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
      const response = await api.logs.queryRuntime({
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
    a.download = `runtime_logs_${selectedTask?.name || 'all'}_job${selectedRunId || 'aggregate'}_${new Date().toISOString()}.txt`;
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
    setJobs([]);
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
    if (jobs.length === 0) return 0;
    const completed = jobs.filter((j) => ['COMPLETED', 'FAILED', 'ABORTED'].includes(j.status)).length;
    return Math.round((completed / jobs.length) * 100);
  }, [jobs]);

  const isLiveMode = !!selectedRunId;

  return (
    <div className={`${embedded ? 'min-h-[72vh]' : 'h-full'} flex flex-col`}>
      {!embedded && (
        <div className="mb-4">
          <PageHeader
            title="日志总览"
            subtitle={`${isLiveMode ? (isConnected ? '实时连接中' : '实时未连接') : '历史聚合模式'}${selectedRunId ? ` | Job: #${selectedRunId}` : ''}`}
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void loadTasks();
                  void loadRuns(selectedTaskId);
                  void handleRefreshLogs();
                }}
              >
                <RefreshCw className="h-4 w-4" />
                刷新
              </Button>
            }
          />
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="w-80 flex-shrink-0">
          <Card className="flex h-full flex-col overflow-hidden">
            <div className={cn('p-3', LIST_ITEM.sectionBorder)}>
              <div className="flex items-center justify-between">
                <h3 className={cn('font-medium', TEXT.heading)}>任务视图</h3>
                <span className={cn('text-xs', TEXT.subtitle)}>{plans.length}</span>
              </div>
              <div className="relative mt-2">
                <Search className={cn('absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2', TEXT.subtitle)} />
                <input
                  value={taskSearch}
                  onChange={(e) => setTaskSearch(e.target.value)}
                  placeholder="搜索工作流"
                  className={FORM.inputSm}
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              <button
                type="button"
                onClick={() => setSelectedTaskId(null)}
                className={listItemClass(selectedTaskId === null, cn('border-b px-4 py-3 text-sm', LIST_ITEM.sectionBorder))}
              >
                <div className="flex items-center justify-between">
                  <span className={cn('font-medium', TEXT.heading)}>全部任务</span>
                  <Database className={cn('h-4 w-4', TEXT.subtitle)} />
                </div>
                <p className={cn('mt-1 text-xs', TEXT.subtitle)}>跨任务聚合日志</p>
              </button>

              {taskLoading ? (
                <div className={cn('p-4 text-center text-sm', TEXT.subtitle)}>加载中...</div>
              ) : filteredTasks.length === 0 ? (
                <div className={cn('p-4 text-center text-sm', TEXT.subtitle)}>无匹配任务</div>
              ) : (
                <div className={LIST_ITEM.divider}>
                  {filteredTasks.map((wf) => (
                    <button
                      key={wf.id}
                      type="button"
                      onClick={() => setSelectedTaskId(wf.id)}
                      className={listItemClass(selectedTaskId === wf.id, 'w-full px-4 py-3')}
                    >
                      <div className="flex items-center justify-between">
                        <span className={cn('truncate text-sm font-medium', TEXT.heading)}>{wf.name}</span>
                      </div>
                      {wf.description && (
                        <div className={cn('mt-1 truncate text-[11px]', TEXT.subtitle)}>{wf.description}</div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>

        <div className="w-72 flex-shrink-0">
          <Card className="flex h-full flex-col overflow-hidden">
            <div className={cn('p-3', LIST_ITEM.sectionBorder)}>
              <div className="flex items-center justify-between">
                <h3 className={cn('font-medium', TEXT.heading)}>执行节点</h3>
                <span className={cn('text-xs', TEXT.subtitle)}>{jobs.length}</span>
              </div>
              <div className="mt-2">
                <div className={cn('mb-1 flex items-center justify-between text-xs', TEXT.subtitle)}>
                  <span>整体进度</span>
                  <span>{overallProgress}%</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-muted">
                  <div className="h-1.5 rounded-full bg-primary" style={{ width: `${overallProgress}%` }} />
                </div>
              </div>
              <div className="relative mt-2">
                <Search className={cn('absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2', TEXT.subtitle)} />
                <input
                  value={runSearch}
                  onChange={(e) => setRunSearch(e.target.value)}
                  placeholder="搜索 Job"
                  className={FORM.inputSm}
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              <button
                type="button"
                onClick={() => setSelectedRunId(null)}
                className={listItemClass(selectedRunId === null, cn('border-b px-3 py-3', LIST_ITEM.sectionBorder))}
              >
                <div className="flex items-center justify-between">
                  <span className={cn('text-sm font-medium', TEXT.heading)}>全部执行节点</span>
                  <Server className={cn('h-4 w-4', TEXT.subtitle)} />
                </div>
                <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>当前任务范围内聚合</p>
              </button>

              {runLoading ? (
                <div className={cn('p-4 text-center text-sm', TEXT.subtitle)}>加载中...</div>
              ) : filteredRuns.length === 0 ? (
                <div className={cn('p-4 text-center text-sm', TEXT.subtitle)}>暂无执行节点</div>
              ) : (
                <div className={LIST_ITEM.divider}>
                  {filteredRuns.map((job) => (
                    <button
                      key={job.id}
                      type="button"
                      onClick={() => setSelectedRunId(job.id)}
                      className={listItemClass(selectedRunId === job.id, 'w-full px-3 py-3')}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5">
                          <Smartphone className={cn('h-4 w-4', TEXT.subtitle)} />
                          <span className={cn('text-sm font-medium', TEXT.heading)}>Job {job.id}</span>
                        </div>
                        <span className={cn('rounded px-2 py-0.5 text-[11px]', STATUS_CHIP.muted)}>{job.status}</span>
                      </div>
                      <div className={cn('mt-1 text-[11px]', TEXT.subtitle)}>
                        {job.run_type} | {job.triggered_by || 'auto'}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>

        <div className="min-w-0 flex-1">
          <Card className="flex h-full flex-col overflow-hidden">
            <div className={cn('p-3', LIST_ITEM.sectionBorder)}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleLoadOlder()}
                  disabled={!hasMore || loadingOlder || historyLoading}
                  className={TOOL_BTN}
                >
                  <ChevronsUp className="h-3.5 w-3.5" />
                  {loadingOlder ? '加载中...' : '加载更早日志'}
                </button>
                <button
                  type="button"
                  onClick={() => void handleRefreshLogs()}
                  disabled={historyLoading}
                  className={TOOL_BTN}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {historyLoading ? '刷新中...' : '刷新历史'}
                </button>
                <button
                  type="button"
                  onClick={() => setAutoScroll((v) => !v)}
                  className={cn(
                    TOOL_BTN,
                    autoScroll && SEGMENTED.itemActive,
                  )}
                >
                  <Radio className="h-3.5 w-3.5" />
                  自动滚动
                </button>
                <button
                  type="button"
                  onClick={clearLogs}
                  className={TOOL_BTN}
                  title="清空已加载日志"
                >
                  <X className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={downloadLogs}
                  disabled={filteredLogs.length === 0}
                  className={TOOL_BTN}
                  title="下载当前过滤结果"
                >
                  <Download className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
                <div className="relative md:col-span-2">
                  <Search className={cn('absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2', TEXT.subtitle)} />
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="关键词（message / step / job）"
                    className={FORM.inputSm}
                  />
                </div>
                <input
                  value={stepFilter}
                  onChange={(e) => setStepFilter(e.target.value)}
                  placeholder="step_id"
                  className={FORM.selectSm}
                />
                <select
                  value={levelFilter}
                  onChange={(e) => setLevelFilter(e.target.value)}
                  className={FORM.selectSm}
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
                  className={FORM.selectSm}
                >
                  <option value="15m">最近 15 分钟</option>
                  <option value="1h">最近 1 小时</option>
                  <option value="6h">最近 6 小时</option>
                  <option value="24h">最近 24 小时</option>
                  <option value="all">全部时间</option>
                </select>
              </div>

              <div className={cn('mt-2 flex flex-wrap items-center gap-3 text-xs', TEXT.subtitle)}>
                <span>范围: {selectedTask ? selectedTask.name : '全部任务'}</span>
                <span>模式: {selectedRun ? `单 Job #${selectedRun.id}` : '聚合模式'}</span>
                <span>已加载: {logs.length}</span>
                <span>过滤后: {filteredLogs.length}</span>
              </div>
            </div>

            <div
              ref={logViewportRef}
              className="dark flex-1 overflow-y-auto bg-background p-2 font-mono text-xs"
              onScroll={handleLogScroll}
            >
              {historyLoading && logs.length === 0 ? (
                <div className={cn('py-8 text-center', TEXT.subtitle)}>加载日志中...</div>
              ) : filteredLogs.length === 0 ? (
                <div className={cn('py-8 text-center', TEXT.subtitle)}>
                  {selectedTaskId === null && !selectedRunId
                    ? '暂无日志，已处于跨任务聚合模式'
                    : selectedRunId
                      ? '等待日志...' : '请选择 Job 或调整过滤条件'}
                </div>
              ) : (
                <div style={{ height: totalHeight, position: 'relative' }}>
                  <div style={{ transform: `translateY(${startIndex * LOG_ROW_HEIGHT}px)` }}>
                    {visibleLogs.map((log, visibleIndex) => (
                      <div
                        key={log.key}
                        className={cn(
                          'grid grid-cols-[64px_minmax(0,1fr)] leading-[22px]',
                          log.level === 'ERROR'
                            ? LOG_LEVEL.rowError
                            : log.level === 'WARN'
                              ? LOG_LEVEL.rowWarn
                              : LOG_LEVEL.rowDefault,
                        )}
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
                        <span className={cn('select-none pr-3 text-right', TEXT.subtitle)}>
                          {startIndex + visibleIndex + 1}
                        </span>
                        <span className="truncate">
                          <span className={TEXT.subtitle}>
                            [{formatLocalDateTime(log.timestamp, {
                              month: '2-digit',
                              day: '2-digit',
                              hour: '2-digit',
                              minute: '2-digit',
                              second: '2-digit',
                            })}]
                          </span>{' '}
                          <span className={LOG_LEVEL.tagJob}>[job-{log.job_id ?? 'na'}]</span>{' '}
                          <span className={LOG_LEVEL.tagStep}>[{log.step_id || '-'}]</span>{' '}
                          <span
                            className={
                              log.level === 'ERROR'
                                ? LOG_LEVEL.error
                                : log.level === 'WARN'
                                  ? LOG_LEVEL.warn
                                  : LOG_LEVEL.tagLevelOk
                            }
                          >
                            [{log.level}]
                          </span>{' '}
                          {highlightLogText(log.message)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className={cn('flex items-center justify-between px-3 py-2 text-xs', PANEL.footer, TEXT.subtitle)}>
              <span>
                {isLiveMode ? (
                  <span
                    className={cn(
                      'inline-flex items-center gap-1',
                      isConnected ? 'text-success' : TEXT.subtitle,
                    )}
                  >
                    <span
                      className={cn(
                        'h-2 w-2 rounded-full',
                        isConnected ? 'bg-success' : 'bg-muted-foreground',
                      )}
                    />
                    {isConnected ? '单 Job 实时订阅已连接' : '单 Job 实时订阅未连接'}
                  </span>
                ) : (
                  <span className={cn('inline-flex items-center gap-1', TEXT.body)}>
                    <Database className="h-3.5 w-3.5" />
                    聚合历史查询模式（非实时）
                  </span>
                )}
              </span>
              <span>has_more: {hasMore ? 'yes' : 'no'}</span>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
